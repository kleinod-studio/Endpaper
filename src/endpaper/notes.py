"""Highlights and notes of a book, in a Markdown file beside it.

Plain logic without any interface. The file has to stay readable without Endpaper and must never
lose anything: text that cannot be assigned to a mark is kept where it is, every change is saved
atomically, and changes made by other programs are merged instead of overwritten. If both sides
changed the same mark, both versions stay. A file that cannot be read as text is left untouched,
and marks are saved to a conflict file beside it instead.

    # Book title

    ## Chapter

    > The quoted text

    The note, as long as it likes.

    <!-- endpaper: id=a1b2c3d4 cfi=epubcfi(/6/4!/4/2) color=yellow style=highlight -->
"""

import copy
import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as url_quote, unquote

from .fileio import atomic_write_text
from .library import conflict_path_for, notes_path_for

ANCHOR = re.compile(r"^<!--\s*endpaper:\s*(.*?)\s*-->\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
VALID_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# Lines of a note that would otherwise be read as part of the file's structure.
STRUCTURE = r"(?:#{1,6}\s|>|<!--\s*endpaper:)"
NEEDS_ESCAPE = re.compile(rf"^(\\*{STRUCTURE})")
ESCAPED = re.compile(rf"^\\(\\*{STRUCTURE})")
CFI_SAFE = "/!:,()[]^~@;=."


class NotesError(Exception):
    """The notes could not be saved. They stay in memory, and the next change tries again."""


@dataclass
class Entry:
    id: str
    cfi: str
    quote: str = ""
    text: str = ""
    chapter: str | None = None
    color: str = "yellow"
    style: str = "highlight"
    conflict: bool = False
    extra: dict[str, str] = field(default_factory=dict)  # settings of a newer version must survive


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Loose:
    """Text that belongs to no mark: written by hand, or a mark that lost its anchor."""

    text: str


def _new_id(taken) -> str:
    while (new := secrets.token_hex(4)) in taken:
        pass
    return new


def _trimmed(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _read_anchor(line: str) -> dict[str, str] | None:
    match = ANCHOR.match(line)
    if not match:
        return None
    attributes = {}
    for part in match[1].split():
        key, separator, value = part.partition("=")
        if not separator:
            return None
        attributes[key] = unquote(value)
    if "cfi" not in attributes or not VALID_ID.match(attributes.get("id", "")):
        return None
    return attributes


def _unescape(line: str) -> str:
    return ESCAPED.sub(r"\1", line)


def _entry_from(lines: list[str], attributes: dict[str, str], chapter: str | None) -> tuple[list[str], Entry]:
    """Splits what stands before an anchor into text that stays loose and the mark itself."""
    start = next((i for i, line in enumerate(lines) if line.startswith(">")), None)
    before, rest = ([], lines) if start is None else (lines[:start], lines[start:])
    quote = []
    while rest and rest[0].startswith(">"):
        quote.append(re.sub(r"^> ?", "", rest[0]))
        rest = rest[1:]
    known = {"id", "cfi", "color", "style", "conflict"}
    return before, Entry(
        id=attributes["id"],
        cfi=attributes["cfi"],
        quote="\n".join(quote),
        text="\n".join(_unescape(line) for line in _trimmed(rest)),
        chapter=chapter,
        color=attributes.get("color", "yellow"),
        style=attributes.get("style", "highlight"),
        conflict=attributes.get("conflict") == "1",
        extra={k: v for k, v in attributes.items() if k not in known},
    )


def parse(text: str) -> list:
    """Any text is valid: what is not understood becomes Loose text."""
    blocks, pending, seen, chapter = [], [], set(), None

    def flush():
        lines = _trimmed(pending)
        if lines:
            blocks.append(Loose("\n".join(lines)))
        pending.clear()

    for line in text.removeprefix("﻿").splitlines():
        attributes = _read_anchor(line)
        if attributes and attributes["id"] not in seen:
            seen.add(attributes["id"])
            before, entry = _entry_from(pending, attributes, chapter)
            pending[:] = before
            flush()
            blocks.append(entry)
        elif not attributes and (heading := HEADING.match(line)):
            flush()
            level, title = len(heading[1]), heading[2]
            blocks.append(Heading(level, title))
            if level >= 2:
                chapter = title
        else:
            pending.append(line)  # also a second mark with the same id: it stays as it is
    flush()
    return blocks


def _write_entry(entry: Entry) -> str:
    parts = []
    if entry.quote:
        parts.append("\n".join(f"> {line}".rstrip() for line in entry.quote.splitlines()))
    if entry.text.strip():
        parts.append("\n".join(NEEDS_ESCAPE.sub(r"\\\1", line) for line in _trimmed(entry.text.splitlines())))
    attributes = {"id": entry.id, "cfi": url_quote(entry.cfi, safe=CFI_SAFE), "color": entry.color, "style": entry.style}
    if entry.conflict:
        attributes["conflict"] = "1"
    attributes.update((key, url_quote(value, safe=CFI_SAFE)) for key, value in entry.extra.items())
    parts.append("<!-- endpaper: " + " ".join(f"{k}={v}" for k, v in attributes.items()) + " -->")
    return "\n\n".join(parts)


def serialize(blocks: list) -> str:
    parts = []
    for block in blocks:
        if isinstance(block, Heading):
            parts.append(f"{'#' * block.level} {block.text}")
        elif isinstance(block, Loose):
            parts.append(block.text)
        else:
            parts.append(_write_entry(block))
    return "\n\n".join(parts) + "\n" if parts else ""


def _insert(blocks: list, entry: Entry) -> None:
    """At the end of the entry's chapter. A chapter that is not there yet goes to the end of the file."""
    if entry.chapter is None:
        index = next((i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.level >= 2), len(blocks))
        blocks.insert(index, entry)
        return
    start = next((i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.level >= 2 and b.text == entry.chapter), None)
    if start is None:
        blocks += [Heading(2, entry.chapter), entry]
        return
    end = next((i for i in range(start + 1, len(blocks)) if isinstance(blocks[i], Heading) and blocks[i].level >= 2), len(blocks))
    blocks.insert(end, entry)


def _entries(blocks: list) -> list[Entry]:
    return [b for b in blocks if isinstance(b, Entry)]


def _find(blocks: list, entry_id: str) -> Entry | None:
    return next((e for e in _entries(blocks) if e.id == entry_id), None)


def _apply_add(blocks: list, entry: Entry) -> None:
    taken = {e.id for e in _entries(blocks)}
    _insert(blocks, replace(entry, id=_new_id(taken)) if entry.id in taken else entry)


def _apply_update(blocks: list, base: Entry, changes: dict) -> None:
    current = _find(blocks, base.id)
    if current is None:  # removed elsewhere, changed here: the change is worth keeping
        _insert(blocks, replace(base, **changes))
        return
    clash = any(getattr(current, k) not in (getattr(base, k), v) for k, v in changes.items())
    if clash:  # both versions stay
        taken = {e.id for e in _entries(blocks)}
        _insert(blocks, replace(current, id=_new_id(taken), conflict=True, **changes))
    else:
        for key, value in changes.items():
            setattr(current, key, value)


def _apply_remove(blocks: list, base: Entry) -> None:
    current = _find(blocks, base.id)
    if current is not None and (current.text, current.quote) == (base.text, base.quote):
        blocks.remove(current)  # what was changed elsewhere meanwhile stays


class NotesFile:
    """The marks of one book. Every change is written at once; a failed write raises NotesError,
    keeps the change in memory and is tried again with the next change, flush() or refresh()."""

    def __init__(self, book_path: Path, title: str = ""):
        self.book_path = book_path
        self.path = notes_path_for(book_path)
        self.title = title
        self.damaged = False  # the file is no text: it stays untouched, marks go to conflict_path
        self.conflict_path: Path | None = None
        self._saved: list = []  # as it is on disk
        self._pending: list = []  # changes not written yet
        self._known: bytes | None = None
        self.blocks: list = []  # as the reader should see it
        self.refresh()

    @property
    def entries(self) -> list[Entry]:
        return _entries(self.blocks)

    @property
    def loose(self) -> list[str]:
        """Text that belongs to no mark. It has to be shown, so that it is not forgotten."""
        return [b.text for b in self.blocks if isinstance(b, Loose)]

    def add(self, cfi: str, quote: str = "", text: str = "", chapter: str | None = None,
            color: str = "yellow", style: str = "highlight") -> Entry:
        chapter = " ".join(chapter.split()) or None if chapter else None
        entry = Entry(_new_id({e.id for e in self.entries}), cfi, quote, text, chapter, color, style)
        self._change(_apply_add, copy.deepcopy(entry))
        return _find(self.blocks, entry.id) or entry

    def update(self, entry_id: str, **changes) -> None:
        unknown = set(changes) - {"text", "color", "style"}
        if unknown:
            raise ValueError(f"cannot change {unknown}")
        base = _find(self.blocks, entry_id)
        if base is None:
            raise KeyError(entry_id)
        self._change(_apply_update, copy.deepcopy(base), changes)

    def remove(self, entry_id: str) -> None:
        base = _find(self.blocks, entry_id)
        if base is None:
            raise KeyError(entry_id)
        self._change(_apply_remove, copy.deepcopy(base))

    def refresh(self) -> bool:
        """Takes over what other programs changed. True if anything did."""
        changed = self._take_disk()
        if self._pending:
            self.flush()
        elif changed:
            self.blocks = copy.deepcopy(self._saved)
        return changed

    def flush(self) -> None:
        unreadable = None
        try:
            self._take_disk()
        except NotesError as error:
            unreadable = error  # nothing is written over a file that could not be read
        working = copy.deepcopy(self._saved)
        for apply, *arguments in self._pending:
            apply(working, *copy.deepcopy(arguments))
        self.blocks = working
        if unreadable:
            raise unreadable
        if not self._pending:
            return
        if not working:
            self._pending.clear()
            return
        if not any(isinstance(b, Heading) and b.level == 1 for b in working) and self.title and self._known is None:
            working.insert(0, Heading(1, self.title))
        text = serialize(working)
        try:
            if self.damaged:
                if self.conflict_path is None:
                    self.conflict_path = self._free_conflict_path()
                atomic_write_text(self.conflict_path, text)
            else:
                atomic_write_text(self.path, text)
                self._known = text.encode("utf-8")
        except OSError as error:
            raise NotesError(str(error)) from error
        self._saved = copy.deepcopy(working)
        self._pending.clear()

    def _change(self, apply, *arguments) -> None:
        self._pending.append((apply, *arguments))
        self.flush()

    def _take_disk(self) -> bool:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            data = None
        except OSError as error:
            raise NotesError(str(error)) from error
        if data == self._known:
            return False
        self._known = data
        try:
            self._saved = parse(data.decode("utf-8")) if data is not None else []
            self.damaged = False
            self.conflict_path = None
        except UnicodeDecodeError:
            self.damaged = True  # what is known so far stays in _saved, and goes to the conflict file
        return True

    def _free_conflict_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate, number = conflict_path_for(self.book_path, stamp), 1
        while candidate.exists():
            number += 1
            candidate = conflict_path_for(self.book_path, f"{stamp}-{number}")
        return candidate
