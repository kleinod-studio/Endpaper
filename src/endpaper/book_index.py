"""The search index of the library: the text of every book, cut into passages, in an SQLite database.

The index is a cache and nothing else. It can be thrown away at any time and is built anew from the
books, which are never changed. It is built in a process of its own at a low priority (see main()),
so that reading goes on undisturbed; the app only reads from it.

Books are not trusted: their documents are read as plain text with a tolerant parser that expands no
entities and loads nothing, sizes are limited, and a book that makes the indexer fail is skipped."""

import json
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from . import epub
from .notes import EXCERPT_CONTEXT, find_in_text
from .snowball.english_stemmer import EnglishStemmer
from .snowball.german_stemmer import GermanStemmer

SCHEMA_VERSION = 2
MAX_BOOK_CHARS = 64_000_000  # more text than any real book has
EXCERPTS_PER_BOOK = 3

# Each passage is the text of the innermost of these around it, as in the reader (see peek.js).
BLOCKS = frozenset("p div li dd dt blockquote td th section article aside figcaption h1 h2 h3 h4 h5 h6 pre body".split())
LEFT_OUT = frozenset("head script style template nav svg math audio video object embed iframe".split())
NOTE_TYPES = frozenset(("footnote", "endnote", "rearnote", "note"))
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


@dataclass
class Passage:
    section: int  # the place in the reading order, as foliate-js counts it
    path: list[int]  # the element, as places among the element children, from the document element down
    text: str


@dataclass
class _Block:
    path: list[int]
    pieces: list[str] = field(default_factory=list)


class _Reader(HTMLParser):
    """Collects the passages of one document. The paths are counted like the document's element tree
    in the reader, so that the app can find the element again."""

    def __init__(self, section: int):
        super().__init__(convert_charrefs=True)
        self.section = section
        self.passages: list[Passage] = []
        self.stack: list[list] = []  # tag, path, number of element children so far, whether it is left out
        self.blocks: list[_Block] = []
        self.left_out = 0  # inside an element whose text is not part of the running text
        self.root_children = 0

    def _enter(self, tag: str, attrs) -> list[int]:
        if self.stack:
            parent = self.stack[-1]
            parent[2] += 1
            return parent[1] + [parent[2] - 1]
        self.root_children += 1
        return [] if self.root_children == 1 else [-1]  # a second root is broken markup

    def handle_starttag(self, tag, attrs):
        path = self._enter(tag, attrs)
        if tag in VOID:
            if tag == "br":
                self.handle_data(" ")
            return
        attributes = dict(attrs)
        types = set((attributes.get("epub:type") or "").split())
        hidden = tag in LEFT_OUT or (tag == "aside" and types & NOTE_TYPES) or attributes.get("hidden") is not None
        left_out = bool(hidden or self.left_out)
        self.stack.append([tag, path, 0, left_out])
        if left_out:
            self.left_out += 1
        elif tag in BLOCKS and path and path[0] >= 0:
            self.handle_data(" ")  # the text around a block inside another one does not run together
            self.blocks.append(_Block(path))

    def handle_startendtag(self, tag, attrs):
        self._enter(tag, attrs)
        if tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag in VOID or not any(entry[0] == tag for entry in self.stack):
            return  # a stray end tag changes nothing
        while self.stack:
            open_tag, path, _n, left_out = self.stack.pop()
            if left_out:
                self.left_out -= 1
            elif self.blocks and self.blocks[-1].path == path:
                self._close(self.blocks.pop())
                self.handle_data(" ")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.blocks and not self.left_out:
            self.blocks[-1].pieces.append(data)

    def _close(self, block: _Block):
        text = " ".join("".join(block.pieces).replace("­", "").split())
        if text:
            self.passages.append(Passage(self.section, block.path, text))

    def finish(self) -> list[Passage]:
        self.close()
        while self.blocks:
            self._close(self.blocks.pop())
        return sorted(self.passages, key=lambda passage: passage.path)


def read_passages(path: Path) -> list[Passage]:
    """The running text of a book in reading order. Raises epub.EpubError for a book that cannot be read."""
    passages, total = [], 0
    for section, data in epub.read_spine(path):
        if data is None:
            continue
        reader = _Reader(section)
        try:
            reader.feed(data.decode("utf-8", errors="replace"))
            found = reader.finish()
        except (AssertionError, ValueError, RecursionError):  # what the parser cannot make sense of
            continue
        total += sum(len(passage.text) for passage in found)
        if total > MAX_BOOK_CHARS:
            break
        passages.extend(found)
    return passages


# --- Word forms ---------------------------------------------------------------------------

WORD = re.compile(r"\w+")
STEMMERS = {"de": GermanStemmer, "en": EnglishStemmer}  # Snowball, see snowball/VENDORED.md


def language_code(language: str | None) -> str | None:
    """"de-DE" and "ger" both mean German, as far as word forms are concerned."""
    code = (language or "").strip().lower().replace("_", "-").split("-")[0]
    code = {"ger": "de", "deu": "de", "eng": "en"}.get(code, code)
    return code if code in STEMMERS else None


@lru_cache(maxsize=None)
def _stemmer(code: str):
    return STEMMERS[code]()


@lru_cache(maxsize=200_000)
def stem(word: str, code: str) -> str:
    """The stem of a word in a language: "Häuser" and "Haus" are both "haus"."""
    return _stemmer(code).stemWord(word.lower())


def stems_of(text: str, code: str | None) -> str:
    return " ".join(stem(word, code) for word in WORD.findall(text)) if code else ""


def folded(word: str) -> str:
    """As the index keeps its words: in lower case and without accents."""
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(c))


def _distance(a: str, b: str, limit: int) -> int:
    """How many letters have to change to turn a into b, or limit + 1 once it is more than limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != other)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def locate(text: str, query: str, code: str | None) -> tuple[tuple[int, int] | None, str | None]:
    """Where the query stands in a passage, and the words as they stand there if they are another form of it."""
    where = find_in_text(text, query)
    if where and (where[0] == 0 or not text[where[0] - 1].isalnum()):
        return where, None
    if code:
        wanted = [stem(word, code) for word in WORD.findall(query)]
        words = list(WORD.finditer(text))
        for i in range(len(words) - len(wanted) + 1):
            if all(stem(words[i + k].group(), code) == wanted[k] for k in range(len(wanted))):
                start, end = words[i].start(), words[i + len(wanted) - 1].end()
                return (start, end), text[start:end]
    return where, None


# --- The reader's marks in a passage ------------------------------------------------------

def _element_steps(steps: str) -> list[int]:
    """The element path of a CFI's steps, up to the first step into a text."""
    path = []
    for step in steps.split("/"):
        number = step.split(":")[0].split("~")[0].split("@")[0]
        if not number.isdigit():
            continue
        if int(number) % 2:
            break
        path.append(int(number) // 2 - 1)
    return path


def cfi_places(cfi: str) -> tuple[int, list[int], list[int]] | None:
    """The section of a mark, and the elements its start and end are in, as the index counts them."""
    if not cfi.startswith("epubcfi(") or not cfi.endswith(")"):
        return None
    inner = re.sub(r"\[(?:\^.|[^\]])*\]", "", cfi[len("epubcfi("):-1])  # assertions such as [id]
    parts = inner.split(",")
    spine, bang, document = parts[0].partition("!")
    spine_steps = [int(step) for step in spine.split("/") if step.isdigit()]
    if not bang or not spine_steps:
        return None
    start = _element_steps(document + (parts[1] if len(parts) > 1 else ""))
    end = _element_steps(document + (parts[2] if len(parts) > 2 else ""))
    return spine_steps[-1] // 2 - 1, start, end


def _within(path: list[int], start: list[int], end: list[int]) -> bool:
    return path == start[:len(path)] or path == end[:len(path)] or start < path < end


def _marked_range(text: str, quote: str) -> tuple[int, int] | None:
    """The part of a passage that a mark covers, from its quote, which may run into the passages around it."""
    quote = " ".join(quote.replace("\u00ad", "").split())
    if not quote:
        return None
    found = text.find(quote)
    if found >= 0:
        return found, found + len(quote)
    if text in quote:
        return 0, len(text)
    for size in range(min(len(text), len(quote), 2000), 2, -1):
        if text.endswith(quote[:size]):  # the mark starts in this passage
            return len(text) - size, len(text)
        if text.startswith(quote[-size:]):  # and ends in this one
            return 0, size
    return None


def excerpt_parts(text: str, start: int, end: int, marked: list[tuple[int, int, str, bool]]) -> list[dict]:
    """The words around a find, cut at whole words, as parts that know whether they are the find
    and how they are marked: (start, end, color, underline) for each mark."""
    low, high = max(0, start - EXCERPT_CONTEXT), min(len(text), end + EXCERPT_CONTEXT)
    if low > 0 and (space := text.find(" ", low, start)) >= 0:
        low = space + 1
    if high < len(text) and (space := text.rfind(" ", end, high)) > end:
        high = space
    parts = []
    if low > 0:
        parts.append({"text": "… "})
    for place in range(low, high):
        color, underline = None, False
        for mark_start, mark_end, mark_color, mark_underline in marked:
            if mark_start <= place < mark_end:
                color, underline = mark_color, mark_underline
        look = {"word": start <= place < end, "color": color, "underline": underline}
        if parts and {k: parts[-1].get(k, False if k != "color" else None) for k in look} == look:
            parts[-1]["text"] += text[place]
        else:
            parts.append({"text": text[place], **{k: v for k, v in look.items() if v}})
    if high < len(text):
        parts.append({"text": " …"})
    return parts


def signature(path: Path) -> str | None:
    """Changes when the file changes, and stays when it is moved."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _match_expression(query: str, codes) -> str | None:
    """The words as a phrase whose last word may go on, so that "Gnade" also finds "Gnadengabe";
    or their stems, so that "Haus" also finds "Häuser", in each language there are books in."""
    words = WORD.findall(query)
    if not words:
        return None
    expressions = ['text : "' + " ".join(words) + '"*']
    expressions += ['stems : "' + " ".join(stem(word, code) for word in words) + '"' for code in sorted(codes)]
    return " OR ".join(dict.fromkeys(expressions))


@dataclass
class Found:
    book: str  # the path of the book
    count: int  # the passages that contain the query
    passages: list[dict]  # the first few: section, path, the excerpt as parts, and query if it is another form


class Index:
    def __init__(self, file: Path, writable: bool = False):
        self.file = file
        if writable:
            file.parent.mkdir(parents=True, exist_ok=True)
            self.db = self._open_for_writing()
        else:
            self.db = sqlite3.connect(f"file:{file}?mode=ro", uri=True, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout = 2000")

    def _open_for_writing(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.file)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            version = -1
        if version != SCHEMA_VERSION:
            db.close()
            for suffix in ("", "-wal", "-shm"):  # a cache from another version is simply built anew
                Path(f"{self.file}{suffix}").unlink(missing_ok=True)
            db = sqlite3.connect(self.file)
            db.executescript(f"""
                PRAGMA journal_mode = WAL;
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, signature TEXT, state TEXT, language TEXT);
                CREATE TABLE places (id INTEGER PRIMARY KEY, book INTEGER, section INTEGER, path TEXT);
                CREATE INDEX places_by_book ON places (book);
                CREATE VIRTUAL TABLE passages USING fts5 (text, stems, tokenize = "unicode61 remove_diacritics 2");
                CREATE VIRTUAL TABLE words USING fts5vocab (passages, 'col');
                PRAGMA user_version = {SCHEMA_VERSION};
            """)
        return db

    def close(self):
        self.db.close()

    # --- Writing, in the indexing process ---------------------------------------------------

    def _forget(self, book_id: int):
        self.db.execute("DELETE FROM passages WHERE rowid IN (SELECT id FROM places WHERE book = ?)", (book_id,))
        self.db.execute("DELETE FROM places WHERE book = ?", (book_id,))
        self.db.execute("DELETE FROM books WHERE id = ?", (book_id,))

    def update(self, books: list[Path], progress=None) -> None:
        """Brings the index in line with these books: new and changed ones are read, gone ones forgotten.
        A moved book keeps its passages. progress(done, total) is called before each book is read."""
        known = {row[1]: row for row in self.db.execute("SELECT id, path, signature, state FROM books")}
        present = {str(path): path for path in books}
        signatures = {name: signature(path) for name, path in present.items()}
        gone = {name: row for name, row in known.items() if name not in present}
        with self.db:
            for name, path in present.items():
                row = known.get(name)
                if row is not None or signatures[name] is None:
                    continue
                moved = next((g for g in gone.values() if g[2] == signatures[name] and g[3] == "done"), None)
                if moved:
                    self.db.execute("UPDATE books SET path = ? WHERE id = ?", (name, moved[0]))
                    del gone[moved[1]]
                    known[name] = (moved[0], name, moved[2], moved[3])
            for row in gone.values():
                self._forget(row[0])
        work = [name for name in present if signatures[name] and (
            known.get(name) is None or known[name][2] != signatures[name])]
        for done, name in enumerate(work):
            if progress:
                progress(done, len(work))
            self._index_book(present[name], signatures[name], known.get(name))
        if progress:
            progress(len(work), len(work))

    def _index_book(self, path: Path, book_signature: str, row) -> None:
        with self.db:
            if row is not None:
                self._forget(row[0])
            # Marked first and committed: should the book bring this process down, it is not tried again.
            book_id = self.db.execute(
                "INSERT INTO books (path, signature, state) VALUES (?, ?, 'reading')", (str(path), book_signature)
            ).lastrowid
        try:
            code = language_code(epub.read_info(path).language)
            passages = read_passages(path)
        except epub.EpubError:
            code, passages = None, None
        with self.db:
            for passage in passages or []:
                place = self.db.execute(
                    "INSERT INTO places (book, section, path) VALUES (?, ?, ?)",
                    (book_id, passage.section, "/".join(map(str, passage.path))),
                ).lastrowid
                self.db.execute(
                    "INSERT INTO passages (rowid, text, stems) VALUES (?, ?, ?)",
                    (place, passage.text, stems_of(passage.text, code)),
                )
            self.db.execute(
                "UPDATE books SET state = ?, language = ? WHERE id = ?",
                ("done" if passages is not None else "failed", code, book_id),
            )

    # --- Reading, in the app ----------------------------------------------------------------

    def _codes(self) -> set[str]:
        return {row[0] for row in self.db.execute("SELECT DISTINCT language FROM books") if row[0] in STEMMERS}

    def search(self, query: str, books: list[str] | None = None, marks: dict[str, list[dict]] | None = None) -> list[Found]:
        """The books whose text contains the query or another form of it, those with the most passages first.
        marks: the reader's marks of a book by its path, {cfi, quote, color, underline}, shown in the excerpts."""
        expression = _match_expression(query, self._codes())
        if not expression:
            return []
        try:
            rows = self.db.execute(
                "SELECT places.id, books.path, books.language FROM passages JOIN places ON places.id = passages.rowid"
                " JOIN books ON books.id = places.book WHERE passages MATCH ? ORDER BY places.id",
                (expression,),
            ).fetchall()
        except sqlite3.Error:
            return []
        wanted = set(books) if books is not None else None
        by_book: dict[str, list[int]] = {}
        languages: dict[str, str | None] = {}
        for place, book, code in rows:
            if wanted is None or book in wanted:
                by_book.setdefault(book, []).append(place)
                languages[book] = code
        found = []
        for book, places in by_book.items():
            book_marks = [(places_of, mark) for mark in (marks or {}).get(book, [])
                          if (places_of := cfi_places(mark.get("cfi", "")))]
            first = places[:EXCERPTS_PER_BOOK]
            placeholders = ",".join("?" * len(first))
            details = self.db.execute(
                f"SELECT places.section, places.path, passages.text FROM places JOIN passages"
                f" ON passages.rowid = places.id WHERE places.id IN ({placeholders}) ORDER BY places.id",
                first,
            ).fetchall()
            passages = []
            for section, element_path, text in details:
                path = [int(n) for n in element_path.split("/") if n]
                where, form = locate(text, query, languages[book])
                marked = []
                for (mark_section, start, end), mark in book_marks:
                    if mark_section == section and _within(path, start, end):
                        if covered := _marked_range(text, mark.get("quote", "")):
                            marked.append((*covered, mark.get("color"), bool(mark.get("underline"))))
                passage = {"section": section, "path": path, "parts": excerpt_parts(text, *(where or (0, 0)), marked)}
                if form:
                    passage["query"] = form  # the search in the book finds the words only as they stand
                passages.append(passage)
            found.append(Found(book, len(places), passages))
        found.sort(key=lambda item: (-item.count, item.book))
        return found

    def suggest(self, query: str) -> str | None:
        """For a query that finds nothing: the same with each word that stands nowhere replaced by the closest
        word in the books, one letter off, two in longer words. None if there is none."""
        words, changed = WORD.findall(query), False
        for i, word in enumerate(words):
            plain = folded(word)
            if len(plain) < 4 or self._has(plain):
                continue  # a short word could be too many others
            limit = 1 if len(plain) < 8 else 2
            try:
                # Words that begin with the same letter: typing errors seldom hit the first one, and the index
                # can look these up without reading all words.
                candidates = self.db.execute(
                    "SELECT term, doc FROM words WHERE col = 'text' AND term >= ? AND term < ?"
                    " AND length(term) BETWEEN ? AND ?",
                    (plain[0], plain[0] + "\uffff", len(plain) - limit, len(plain) + limit),
                ).fetchall()
            except sqlite3.Error:
                return None
            scored = [(_distance(plain, term, limit), -count, term) for term, count in candidates]
            best = min((item for item in scored if item[0] <= limit), default=None)
            if best:
                words[i], changed = best[2], True
        return " ".join(words) if changed else None

    def _has(self, word: str) -> bool:
        try:
            return self.db.execute(
                "SELECT 1 FROM passages WHERE passages MATCH ? LIMIT 1", (f'text : "{word}"*',)).fetchone() is not None
        except sqlite3.Error:
            return True

    def state(self, book: str) -> str | None:
        """'done', 'failed', 'reading' while it is read, or None for a book not known yet."""
        row = self.db.execute("SELECT state FROM books WHERE path = ?", (book,)).fetchone()
        return row[0] if row else None


def main(argv: list[str]) -> int:
    """The indexing process: endpaper.book_index <index file> <book>... reads the paths of the books,
    one per line, from standard input and reports its progress as lines of JSON."""
    try:
        os.nice(15)
    except OSError:
        pass
    file = Path(argv[1])
    books = [Path(line) for line in sys.stdin.read().splitlines() if line]
    index = Index(file, writable=True)

    def progress(done, total):
        print(json.dumps({"done": done, "total": total}), flush=True)

    try:
        index.update(books, progress)
    finally:
        index.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
