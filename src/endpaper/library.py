"""The library folder: subfolders are categories, .epub files are books.

Plain logic without any interface. A book's notes file moves with it, nothing is ever
overwritten, and deleting always goes through the system trash."""

import errno
import glob
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .fileio import atomic_write_text

BOOK_SUFFIX = ".epub"
NOTES_SUFFIX = ".notes.md"
CONFLICT_INFIX = ".notes-conflict-"
SHELF_FILE = ".endpaper.json"


class LibraryError(Exception):
    """An operation could not be done. The message is shown to the user as a reason code."""

    def __init__(self, code: str, name: str = ""):
        super().__init__(f"{code}: {name}")
        self.code = code  # "exists", "invalid-name", "not-found"
        self.name = name


@dataclass(frozen=True)
class Book:
    path: Path
    category: Path  # the category folder; the library root for books without a category

    @property
    def notes_path(self) -> Path:
        return notes_path_for(self.path)

    @property
    def file_key(self) -> str:
        """The name inside its category, used for pins."""
        return self.path.relative_to(self.category).as_posix()


@dataclass
class Category:
    path: Path
    name: str | None  # None: the books directly in the library folder
    books: list[Book] = field(default_factory=list)
    pinned: list[str] = field(default_factory=list)  # file keys, see Book.file_key

    def is_pinned(self, book: Book) -> bool:
        return book.file_key in self.pinned


def notes_path_for(book_path: Path) -> Path:
    return book_path.with_name(book_path.stem + NOTES_SUFFIX)


def conflict_path_for(book_path: Path, stamp: str) -> Path:
    return book_path.with_name(f"{book_path.stem}{CONFLICT_INFIX}{stamp}.md")


def conflict_paths_for(book_path: Path) -> list[Path]:
    """Notes that could not go into the notes file. They belong to the book just the same."""
    return sorted(book_path.parent.glob(f"{glob.escape(book_path.stem)}{CONFLICT_INFIX}*.md"))


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _is_inside(path: Path, root: Path) -> bool:
    """Follows symlinks: nothing outside the library is shown or touched."""
    try:
        return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))
    except OSError:
        return False


def _collect_books(folder: Path, category: Path, root: Path, recursive: bool) -> list[Book]:
    books = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return books
    for entry in entries:
        if _is_hidden(entry.name):
            continue
        path = Path(entry.path)
        if not _is_inside(path, root):
            continue
        try:
            if entry.is_file() and entry.name.lower().endswith(BOOK_SUFFIX):
                books.append(Book(path, category))
            elif recursive and entry.is_dir():  # deeper folders belong to their category
                books.extend(_collect_books(path, category, root, True))
        except OSError:
            continue
    return books


def read_pins(folder: Path) -> list[str]:
    try:
        data = json.loads((folder / SHELF_FILE).read_text(encoding="utf-8"))
        pinned = data.get("pinned", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [item for item in pinned if isinstance(item, str)] if isinstance(pinned, list) else []


def write_pins(folder: Path, pinned: list[str]) -> None:
    file = folder / SHELF_FILE
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data["pinned"] = pinned
    atomic_write_text(file, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def scan(root: Path) -> list[Category]:
    """Categories in name order; the books without a category come first, if there are any."""
    result = []
    loose = _collect_books(root, root, root, False)
    if loose:
        result.append(Category(root, None, loose, read_pins(root)))
    folders = []
    try:
        for entry in os.scandir(root):
            if not _is_hidden(entry.name) and entry.is_dir() and _is_inside(Path(entry.path), root):
                folders.append(Path(entry.path))
    except OSError:
        return result
    for folder in sorted(folders, key=lambda p: p.name.casefold()):
        result.append(Category(folder, folder.name, _collect_books(folder, folder, root, True), read_pins(folder)))
    return result


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or name.startswith(".") or "/" in name or "\0" in name or len(name.encode("utf-8")) > 240:
        raise LibraryError("invalid-name", name)
    return name


def create_category(root: Path, name: str) -> Path:
    name = _check_name(name)
    path = root / name
    try:
        path.mkdir()
    except FileExistsError as error:
        raise LibraryError("exists", name) from error
    return path


def rename_category(path: Path, name: str) -> Path:
    name = _check_name(name)
    target = path.with_name(name)
    if target == path:
        return path
    if target.exists() and not _same_file(target, path):
        raise LibraryError("exists", name)
    os.rename(path, target)
    return target


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)  # renaming "a" to "A" on a case-insensitive file system
    except OSError:
        return False


def _move_no_overwrite(source: Path, target: Path) -> None:
    if target.exists():
        raise LibraryError("exists", target.name)
    try:
        os.rename(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        _copy_no_overwrite(source, target)
        source.unlink()


def _copy_no_overwrite(source: Path, target: Path) -> None:
    """Copies to a temporary file first, so that an interrupted copy leaves no half book behind."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(source, tmp)
        os.link(tmp, target)  # fails if the target appeared in the meantime; never replaces it
    except FileExistsError as error:
        raise LibraryError("exists", target.name) from error
    finally:
        os.unlink(tmp)


def _move_book_files(book: Book, target: Path) -> None:
    """Moves the book and everything with its notes, or nothing."""
    pairs = [(book.path, target)]
    if book.notes_path.exists():
        pairs.append((book.notes_path, notes_path_for(target)))
    for path in conflict_paths_for(book.path):
        pairs.append((path, target.with_name(target.stem + path.name[len(book.path.stem):])))
    for _source, destination in pairs:
        if destination.exists():
            raise LibraryError("exists", destination.name)
    moved = []
    try:
        for source, destination in pairs:
            _move_no_overwrite(source, destination)
            moved.append((source, destination))
    except OSError:
        for source, destination in reversed(moved):
            _move_no_overwrite(destination, source)
        raise


def _unpin(category: Path, key: str) -> bool:
    pins = read_pins(category)
    if key not in pins:
        return False
    write_pins(category, [p for p in pins if p != key])
    return True


def move_book(book: Book, category: Path) -> Book:
    """Moves a book to the top level of a category. A pin moves along."""
    moved = Book(category / book.path.name, category)
    if moved.path == book.path:
        return book
    _move_book_files(book, moved.path)
    if _unpin(book.category, book.file_key):
        write_pins(category, read_pins(category) + [moved.file_key])
    return moved


def rename_book(book: Book, name: str) -> Book:
    """name is without the .epub ending."""
    name = _check_name(name)
    if name.lower().endswith(BOOK_SUFFIX):
        name = name[: -len(BOOK_SUFFIX)]
    renamed = Book(book.path.with_name(_check_name(name) + BOOK_SUFFIX), book.category)
    if renamed.path == book.path:
        return book
    _move_book_files(book, renamed.path)
    pins = read_pins(book.category)
    if book.file_key in pins:
        write_pins(book.category, [renamed.file_key if p == book.file_key else p for p in pins])
    return renamed


def add_book(source: Path, category: Path) -> Book:
    """Copies a file into the library; the original stays where it is."""
    target = category / source.name
    if target.exists():
        raise LibraryError("exists", target.name)
    _copy_no_overwrite(source, target)
    return Book(target, category)


def set_pinned(book: Book, pinned: bool) -> None:
    pins = [p for p in read_pins(book.category) if p != book.file_key]
    if pinned:
        pins.append(book.file_key)
    write_pins(book.category, pins)


def trash_paths_for_book(book: Book) -> list[Path]:
    """What goes to the trash with a book: its notes too, and they can be restored together."""
    notes = [book.notes_path] if book.notes_path.exists() else []
    return [book.path] + notes + conflict_paths_for(book.path)
