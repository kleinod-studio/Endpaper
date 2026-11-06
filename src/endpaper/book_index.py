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
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import epub
from .notes import excerpt, find_in_text

SCHEMA_VERSION = 1
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


def signature(path: Path) -> str | None:
    """Changes when the file changes, and stays when it is moved."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _match_expression(query: str) -> str | None:
    """The words as a phrase whose last word may go on, so that "Gnade" also finds "Gnadengabe"."""
    words = re.findall(r"\w+", query)
    if not words:
        return None
    return '"' + " ".join(words).replace('"', '""') + '"*'


@dataclass
class Found:
    book: str  # the path of the book
    count: int  # the passages that contain the query
    passages: list[dict]  # the first few: section, path and the excerpt as parts


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
                CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT UNIQUE, signature TEXT, state TEXT);
                CREATE TABLE places (id INTEGER PRIMARY KEY, book INTEGER, section INTEGER, path TEXT);
                CREATE INDEX places_by_book ON places (book);
                CREATE VIRTUAL TABLE passages USING fts5 (text, tokenize = "unicode61 remove_diacritics 2");
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
            passages = read_passages(path)
        except epub.EpubError:
            passages = None
        with self.db:
            for passage in passages or []:
                place = self.db.execute(
                    "INSERT INTO places (book, section, path) VALUES (?, ?, ?)",
                    (book_id, passage.section, "/".join(map(str, passage.path))),
                ).lastrowid
                self.db.execute("INSERT INTO passages (rowid, text) VALUES (?, ?)", (place, passage.text))
            self.db.execute("UPDATE books SET state = ? WHERE id = ?", ("done" if passages is not None else "failed", book_id))

    # --- Reading, in the app ----------------------------------------------------------------

    def search(self, query: str, books: list[str] | None = None) -> list[Found]:
        """The books whose text contains the query, those with the most passages first."""
        expression = _match_expression(query)
        if not expression:
            return []
        try:
            rows = self.db.execute(
                "SELECT places.id, books.path FROM passages JOIN places ON places.id = passages.rowid"
                " JOIN books ON books.id = places.book WHERE passages MATCH ? ORDER BY places.id",
                (expression,),
            ).fetchall()
        except sqlite3.Error:
            return []
        wanted = set(books) if books is not None else None
        by_book: dict[str, list[int]] = {}
        for place, book in rows:
            if wanted is None or book in wanted:
                by_book.setdefault(book, []).append(place)
        found = []
        for book, places in by_book.items():
            first = places[:EXCERPTS_PER_BOOK]
            marks = ",".join("?" * len(first))
            details = self.db.execute(
                f"SELECT places.section, places.path, passages.text FROM places JOIN passages"
                f" ON passages.rowid = places.id WHERE places.id IN ({marks}) ORDER BY places.id",
                first,
            ).fetchall()
            passages = []
            for section, element_path, text in details:
                where = find_in_text(text, query)
                parts = excerpt(text, *where) if where else excerpt(text, 0, 0)
                passages.append({"section": section, "path": [int(n) for n in element_path.split("/") if n],
                                 "parts": parts})
            found.append(Found(book, len(places), passages))
        found.sort(key=lambda item: (-item.count, item.book))
        return found

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
