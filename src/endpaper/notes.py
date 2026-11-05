"""Highlights and notes of a book, in a Markdown file beside it.

Plain logic without any interface. The file has to stay readable without Endpaper and must never
lose anything: text that cannot be assigned to a mark is kept where it is, every change is saved
atomically, and changes made by other programs are merged instead of overwritten. If both sides
changed the same mark, both versions stay. A file that cannot be read as text is left untouched,
and marks are saved to a conflict file beside it instead.

    # Book title

    ## Chapter

    > The quoted text

    <!-- endpaper-note id=n1 -->
    A note in plain Markdown, as long as it likes.
    <!-- endpaper-note-end -->

    <!-- endpaper: id=a1b2c3d4 cfi=epubcfi(/6/4!/4/2) color=yellow style=highlight -->

Older files have the text of a single note directly between the quote and the anchor. They stay
readable, and a mark takes the current form when the file is saved.
"""

import copy
import hashlib
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
NOTE_OPEN = re.compile(r"^<!--\s*endpaper-note\b(?!-)(.*?)-->\s*$")
NOTE_END = re.compile(r"^<!--\s*endpaper-note-end\s*-->\s*$")
# Inside a note only these lines need a backslash: all other Markdown stays as it is.
IN_NOTE_NEEDS_ESCAPE = re.compile(r"^(\\*<!--\s*endpaper)")
IN_NOTE_ESCAPED = re.compile(r"^\\(\\*<!--\s*endpaper)")
# Older files: lines of a note that were written with a backslash.
STRUCTURE = r"(?:#{1,6}\s|>|<!--\s*endpaper:)"
ESCAPED = re.compile(rf"^\\(\\*{STRUCTURE})")
CFI_SAFE = "/!:,()[]^~@;=."
UNDO_LIMIT = 200
MARK_COLORS = {"yellow": "#f5c211", "green": "#33d17a", "blue": "#62a0ea", "pink": "#f08cc0", "purple": "#b28cf0"}
DEFAULT_MARK_COLOR = "yellow"


class NotesError(Exception):
    """The notes could not be saved. They stay in memory, and the next change tries again."""


class NotesKept(Exception):
    """An undo or redo was left out because it would have taken away something written since."""


@dataclass
class Note:
    id: str
    text: str
    conflict: bool = False  # a second version, written while the first changed elsewhere


@dataclass
class Entry:
    id: str
    cfi: str
    quote: str = ""
    notes: list[Note] = field(default_factory=list)
    chapter: str | None = None
    color: str = "yellow"
    style: str = "highlight"
    conflict: bool = False
    extra: dict[str, str] = field(default_factory=dict)  # settings of a newer version must survive

    @property
    def has_note(self) -> bool:
        return any(note.text.strip() for note in self.notes)

    def note(self, note_id: str) -> Note | None:
        return next((note for note in self.notes if note.id == note_id), None)


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


def _read_attributes(text: str) -> dict[str, str]:
    return {key: value for key, _sep, value in (part.partition("=") for part in text.split()) if _sep}


def _notes_from(lines: list[str], entry_id: str) -> list[Note]:
    """Notes in their markers, and text outside of them, which is a note of an older file or written by hand."""
    found, loose, current, attributes = [], [], None, {}

    def flush_loose():
        text = "\n".join(_unescape(line) for line in _trimmed(loose))
        if text:
            found.append(({}, text))
        loose.clear()

    for line in lines:
        if current is None:
            opening = NOTE_OPEN.match(line)
            if opening:
                flush_loose()
                current, attributes = [], _read_attributes(opening[1])
            else:
                loose.append(line)
        elif NOTE_END.match(line):
            found.append((attributes, "\n".join(_trimmed(current))))
            current = None
        else:
            current.append(IN_NOTE_ESCAPED.sub(r"\1", line))
    if current is not None:  # the end marker went missing
        found.append((attributes, "\n".join(_trimmed(current))))
    flush_loose()

    notes, taken = [], set()
    for number, (note_attributes, text) in enumerate(found):
        if not text.strip():
            continue
        note_id = note_attributes.get("id", "")
        if not VALID_ID.match(note_id) or note_id in taken:
            # Always the same for the same place, so that merging can recognize it.
            note_id = hashlib.sha1(f"{entry_id}:{number}".encode()).hexdigest()[:8]
        taken.add(note_id)
        notes.append(Note(note_id, text, note_attributes.get("conflict") == "1"))
    return notes


def _quote_start(lines: list[str]) -> int | None:
    in_note = False
    for number, line in enumerate(lines):
        if in_note:
            in_note = not NOTE_END.match(line)
        elif NOTE_OPEN.match(line):
            in_note = True
        elif line.startswith(">"):
            return number
    return None


def _entry_from(lines: list[str], attributes: dict[str, str], chapter: str | None) -> tuple[list[str], Entry]:
    """Splits what stands before an anchor into text that stays loose and the mark itself."""
    start = _quote_start(lines)
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
        notes=_notes_from(rest, attributes["id"]),
        chapter=chapter,
        color=attributes.get("color", "yellow"),
        style=attributes.get("style", "highlight"),
        conflict=attributes.get("conflict") == "1",
        extra={k: v for k, v in attributes.items() if k not in known},
    )


def parse(text: str) -> list:
    """Any text is valid: what is not understood becomes Loose text."""
    blocks, pending, seen, chapter, in_note = [], [], set(), None, False

    def flush():
        lines = _trimmed(pending)
        if lines:
            blocks.append(Loose("\n".join(lines)))
        pending.clear()

    for line in text.removeprefix("﻿").splitlines():
        attributes = _read_anchor(line)
        if in_note:  # a heading in a note is Markdown of the note. An anchor means its end marker went missing.
            in_note = not (NOTE_END.match(line) or attributes)
        elif NOTE_OPEN.match(line):
            in_note = True
            pending.append(line)
            continue
        if attributes and attributes["id"] not in seen:
            seen.add(attributes["id"])
            before, entry = _entry_from(pending, attributes, chapter)
            pending[:] = before
            flush()
            blocks.append(entry)
        elif not attributes and not in_note and not NOTE_END.match(line) and (heading := HEADING.match(line)):
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
    for note in entry.notes:
        if note.text.strip():
            opening = f"id={note.id}" + (" conflict=1" if note.conflict else "")
            body = "\n".join(IN_NOTE_NEEDS_ESCAPE.sub(r"\\\1", line) for line in _trimmed(note.text.splitlines()))
            parts.append(f"<!-- endpaper-note {opening} -->\n{body}\n<!-- endpaper-note-end -->")
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
    if current is None:
        return
    same_notes = [n.text for n in current.notes] == [n.text for n in base.notes]
    if current.quote == base.quote and same_notes:
        blocks.remove(current)  # what was changed elsewhere meanwhile stays


def _apply_restore(blocks: list, entry: Entry, after: str | None) -> None:
    """Puts a removed mark back where it was, as far as that place still exists."""
    if _find(blocks, entry.id):
        return
    previous = _find(blocks, after) if after else None
    if previous:
        blocks.insert(next(i for i, b in enumerate(blocks) if b is previous) + 1, entry)
    else:
        _insert(blocks, entry)


def _apply_restore_note(blocks: list, base: Entry, note: Note, index: int) -> None:
    current = _find(blocks, base.id)
    if current is None:
        _insert(blocks, base)
    elif current.note(note.id) is None:
        current.notes.insert(min(index, len(current.notes)), note)


def _apply_add_note(blocks: list, base: Entry, note: Note) -> None:
    current = _find(blocks, base.id)
    if current is None:  # removed elsewhere, written here: the note is worth keeping
        _insert(blocks, replace(base, notes=[*base.notes, note]))
    else:
        if current.note(note.id):
            note = replace(note, id=_new_id({n.id for n in current.notes}))
        current.notes.append(note)


def _apply_update_note(blocks: list, base: Entry, note_id: str, old_text: str, text: str) -> None:
    current = _find(blocks, base.id)
    if current is None:
        current = replace(base, notes=[n for n in base.notes if n.id != note_id])
        _insert(blocks, current)
    note = current.note(note_id)
    if note is None:
        current.notes.append(Note(note_id, text))
    elif note.text in (old_text, text):
        note.text = text
    else:  # both versions stay
        current.notes.append(Note(_new_id({n.id for n in current.notes}), text, conflict=True))


def _apply_remove_note(blocks: list, base: Entry, note_id: str, old_text: str) -> None:
    current = _find(blocks, base.id)
    note = current.note(note_id) if current else None
    if note is not None and note.text == old_text:
        current.notes.remove(note)  # what was changed elsewhere meanwhile stays


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
        self._undo: list = []  # (undo, redo), each a function that makes the change
        self._redo: list = []
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
        notes = [Note(secrets.token_hex(4), text)] if text.strip() else []
        entry = Entry(_new_id({e.id for e in self.entries}), cfi, quote, notes, chapter, color, style)
        saved = copy.deepcopy(entry)
        self._record(lambda: self._take_back(saved), lambda: self._restore(saved, None))
        self._change(_apply_add, copy.deepcopy(entry))
        return _find(self.blocks, entry.id) or entry

    def update(self, entry_id: str, **changes) -> None:
        unknown = set(changes) - {"color", "style"}
        if unknown:
            raise ValueError(f"cannot change {unknown}")
        base = self._entry(entry_id)
        before = {key: getattr(base, key) for key in changes}
        self._record(lambda: self._update(entry_id, before), lambda: self._update(entry_id, changes))
        self._update(entry_id, changes)

    def remove(self, entry_id: str) -> None:
        base = copy.deepcopy(self._entry(entry_id))
        entries = self.entries
        place = next(i for i, e in enumerate(entries) if e.id == entry_id)
        after = entries[place - 1].id if place else None
        self._record(lambda: self._restore(base, after), lambda: self._take_back(base))
        self._remove(entry_id)

    def _update(self, entry_id: str, changes: dict) -> None:
        self._change(_apply_update, copy.deepcopy(self._entry(entry_id)), changes)

    def _remove(self, entry_id: str) -> None:
        self._change(_apply_remove, copy.deepcopy(self._entry(entry_id)))

    def _take_back(self, original: Entry) -> None:
        """Removes a mark only as it was: notes written since then must not go with it."""
        self.refresh()
        current = self._entry(original.id)
        if [n.text for n in current.notes] != [n.text for n in original.notes]:
            raise NotesKept(original.id)
        self._change(_apply_remove, copy.deepcopy(original))

    def _take_back_note(self, entry_id: str, kept: Note) -> None:
        self.refresh()
        current = self._entry(entry_id)
        note = current.note(kept.id)
        if note is None:
            raise KeyError(kept.id)
        if note.text != kept.text:
            raise NotesKept(entry_id)
        self._change(_apply_remove_note, copy.deepcopy(current), kept.id, kept.text)

    def _restore(self, entry: Entry, after: str | None) -> None:
        self._change(_apply_restore, copy.deepcopy(entry), after)

    def _record(self, undo, redo) -> None:
        self._undo = [*self._undo, (undo, redo)][-UNDO_LIMIT:]
        self._redo = []

    def undo(self) -> bool:
        """Takes back the last change to a mark. False if there is none. Writing in a note is not part
        of this: its field has its own undo. Raises NotesKept instead of losing what was written since;
        that step is then used up, so the next undo goes on with the one before."""
        return self._step(self._undo, self._redo, 0)

    def redo(self) -> bool:
        return self._step(self._redo, self._undo, 1)

    def _step(self, source: list, target: list, which: int) -> bool:
        while source:
            step = source.pop()
            try:
                step[which]()
            except KeyError:
                continue  # taken away elsewhere meanwhile: on to the step before
            except NotesError:
                target.append(step)  # done in memory, and written as soon as that works again
                raise
            target.append(step)
            return True
        return False

    def _entry(self, entry_id: str) -> Entry:
        base = _find(self.blocks, entry_id)
        if base is None:
            raise KeyError(entry_id)
        return base

    def add_note(self, entry_id: str, text: str) -> Note:
        base = self._entry(entry_id)
        note = Note(_new_id({n.id for n in base.notes}), text)
        self._change(_apply_add_note, copy.deepcopy(base), copy.copy(note))
        return note

    def update_note(self, entry_id: str, note_id: str, text: str) -> None:
        base = self._entry(entry_id)
        note = base.note(note_id)
        if note is None:
            raise KeyError(note_id)
        self._change(_apply_update_note, copy.deepcopy(base), note_id, note.text, text)

    def remove_note(self, entry_id: str, note_id: str, undoable: bool = False) -> None:
        """undoable: a note taken away on purpose can come back. A field emptied by typing has its own undo."""
        base = self._entry(entry_id)
        note = base.note(note_id)
        if note is None:
            raise KeyError(note_id)
        if undoable:
            saved, kept, index = copy.deepcopy(base), copy.copy(note), base.notes.index(note)
            self._record(
                lambda: self._change(_apply_restore_note, copy.deepcopy(saved), copy.copy(kept), index),
                lambda: self._take_back_note(entry_id, kept),
            )
        self._change(_apply_remove_note, copy.deepcopy(base), note_id, note.text)

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
