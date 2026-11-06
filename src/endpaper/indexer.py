"""Keeps the search index of the library up to date, in a process of its own (see book_index.main)."""

import json
import re
import sqlite3
import sys
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from . import config
from .book_index import Index
from .notes import MARK_COLORS

CODE = str(Path(__file__).resolve().parent.parent)  # where the endpaper package lies


_tints_installed = False


def install_note_tints() -> None:
    """The background of a note in a list of results, in the color of its mark."""
    global _tints_installed
    if _tints_installed:
        return
    _tints_installed = True
    provider = Gtk.CssProvider()
    provider.load_from_string("".join(
        f".note-tint-{name} {{ background-color: alpha({hex_color}, 0.32); }}" for name, hex_color in MARK_COLORS.items()
    ))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def parts_markup(parts: list[dict]) -> str:
    """An excerpt with the found word in bold and the reader's marks in their color, as in the book."""
    out = []
    for part in parts:
        text = GLib.markup_escape_text(part.get("text") or "")
        if part.get("word"):
            text = f"<b>{text}</b>"
        color = part.get("color")
        if color and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            text = (
                f'<span underline="single" underline_color="{color}">{text}</span>' if part.get("underline")
                else f'<span background="{color}" bgalpha="45%">{text}</span>'
            )
        out.append(text)
    return "".join(out)


class Indexer:
    """update(books) brings the index in line with the books; on_progress(done, total) tells how far
    it is, with done == total once it is finished. search() reads what is there so far."""

    def __init__(self, on_progress=None, file: Path | None = None):
        self.file = file or config.cache_dir() / "search.db"
        self.on_progress = on_progress
        self.process: Gio.Subprocess | None = None
        self.waiting: list[Path] | None = None  # books to look at once the running pass is done
        self.done = self.total = 0
        self._index: Index | None = None

    @property
    def busy(self) -> bool:
        return self.process is not None

    def update(self, books: list[Path]) -> None:
        if self.process:
            self.waiting = books
            return
        try:
            self.process = Gio.Subprocess.new(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); from endpaper import book_index;"
                 " sys.exit(book_index.main(sys.argv[1:]))",
                 CODE, str(self.file)],
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE,
            )
        except GLib.Error:
            self.process = None
            return
        stdin = self.process.get_stdin_pipe()
        stdin.write_all_async(
            "".join(f"{path}\n" for path in books).encode("utf-8"), GLib.PRIORITY_DEFAULT, None,
            lambda stream, result: self._close_input(stream, result),
        )
        self._read(Gio.DataInputStream.new(self.process.get_stdout_pipe()), self.process)
        self.process.wait_async(None, self._finished)

    @staticmethod
    def _close_input(stream, result):
        try:
            stream.write_all_finish(result)
        except GLib.Error:
            pass
        stream.close_async(GLib.PRIORITY_DEFAULT, None, None)

    def _read(self, lines: Gio.DataInputStream, process):
        def got(stream, result):
            try:
                line, _length = stream.read_line_finish_utf8(result)
            except GLib.Error:
                return
            if line is None or process is not self.process:
                return
            try:
                report = json.loads(line)
                self.done, self.total = int(report["done"]), int(report["total"])
            except (ValueError, KeyError, TypeError):
                pass
            else:
                if self.done < self.total and self.on_progress:
                    self.on_progress(self.done, self.total)
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, got)

        lines.read_line_async(GLib.PRIORITY_DEFAULT, None, got)

    def _finished(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        if process is not self.process:
            return
        self.process = None
        self._reopen()
        waiting, self.waiting = self.waiting, None
        if waiting is not None:
            self.update(waiting)
            return
        self.done = self.total
        if self.on_progress:
            self.on_progress(self.total, self.total)

    def _reopen(self):
        if self._index:
            self._index.close()
            self._index = None

    def _open(self) -> Index | None:
        if self._index is None and self.file.exists():
            try:
                self._index = Index(self.file)
            except sqlite3.Error:
                self._index = None
        return self._index

    def search(self, query: str, books: list[str] | None = None, marks: dict | None = None):
        """See Index.search. marks: the reader's marks by book, to show in the excerpts."""
        if (index := self._open()) is None:
            return []
        try:
            return index.search(query, books, marks)
        except sqlite3.Error:
            self._reopen()
            return []

    def suggest(self, query: str) -> str | None:
        """See Index.suggest: the closest words to a query that finds nothing."""
        if (index := self._open()) is None:
            return None
        try:
            return index.suggest(query)
        except sqlite3.Error:
            self._reopen()
            return None

    def shutdown(self):
        if self.process:
            self.process.force_exit()
            self.process = None
        self._reopen()
