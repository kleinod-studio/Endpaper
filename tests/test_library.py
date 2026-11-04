import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from endpaper import library  # noqa: E402
from endpaper.library import Book, LibraryError  # noqa: E402


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, relative: str, category: str | None = None) -> Book:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"book " + relative.encode())
        return Book(path, self.root / category if category else self.root)

    def test_scan_structure(self):
        self.book("loose.epub")
        self.book("Romane/b.epub")
        self.book("Romane/a.EPUB")
        (self.root / "Leer").mkdir()
        (self.root / ".versteckt").mkdir()
        self.book(".versteckt/x.epub")
        self.book("Romane/.hidden.epub")
        (self.root / "Romane" / "notiz.txt").write_text("x")
        cats = library.scan(self.root)
        self.assertEqual([c.name for c in cats], [None, "Leer", "Romane"])
        self.assertEqual([b.path.name for b in cats[0].books], ["loose.epub"])
        self.assertEqual(cats[1].books, [])
        self.assertEqual({b.path.name for b in cats[2].books}, {"a.EPUB", "b.epub"})

    def test_no_loose_section_without_loose_books(self):
        (self.root / "Romane").mkdir()
        self.assertEqual([c.name for c in library.scan(self.root)], ["Romane"])

    def test_deeper_folders_belong_to_their_category(self):
        self.book("Romane/Reihe/Band 1.epub")
        cats = library.scan(self.root)
        self.assertEqual([c.name for c in cats], ["Romane"])
        self.assertEqual(cats[0].books[0].category, self.root / "Romane")
        self.assertEqual(cats[0].books[0].file_key, "Reihe/Band 1.epub")

    def test_symlink_out_of_the_library_is_not_shown(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.epub").write_bytes(b"x")
        os.symlink(outside, self.root / "link")
        os.symlink(outside / "secret.epub", self.root / "secret.epub")
        self.assertEqual(library.scan(self.root), [])

    def test_create_category(self):
        library.create_category(self.root, "  Romane ")
        self.assertTrue((self.root / "Romane").is_dir())
        with self.assertRaises(LibraryError) as caught:
            library.create_category(self.root, "Romane")
        self.assertEqual(caught.exception.code, "exists")

    def test_bad_names_are_refused(self):
        for name in ("", "  ", ".hidden", "a/b", "../raus", "x" * 300):
            with self.assertRaises(LibraryError, msg=name):
                library.create_category(self.root, name)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_rename_category_refuses_existing(self):
        a = library.create_category(self.root, "A")
        library.create_category(self.root, "B")
        with self.assertRaises(LibraryError):
            library.rename_category(a, "B")
        self.assertEqual(library.rename_category(a, "C"), self.root / "C")

    def test_move_book_takes_notes_and_pin_along(self):
        (self.root / "B").mkdir()
        book = self.book("A/x.epub", "A")
        book.notes_path.write_text("Meine Notizen", encoding="utf-8")
        library.set_pinned(book, True)
        moved = library.move_book(book, self.root / "B")
        self.assertFalse(book.path.exists())
        self.assertFalse(book.notes_path.exists())
        self.assertEqual(moved.notes_path.read_text(encoding="utf-8"), "Meine Notizen")
        self.assertEqual(library.read_pins(self.root / "A"), [])
        self.assertEqual(library.read_pins(self.root / "B"), ["x.epub"])

    def test_move_never_overwrites(self):
        book = self.book("A/x.epub", "A")
        other = self.book("B/x.epub", "B")
        with self.assertRaises(LibraryError):
            library.move_book(book, self.root / "B")
        self.assertEqual(other.path.read_bytes(), b"book B/x.epub")
        self.assertTrue(book.path.exists())

    def test_move_stops_if_the_notes_would_overwrite(self):
        book = self.book("A/x.epub", "A")
        book.notes_path.write_text("mine")
        (self.root / "B").mkdir()
        library.notes_path_for(self.root / "B" / "x.epub").write_text("other")
        with self.assertRaises(LibraryError):
            library.move_book(book, self.root / "B")
        self.assertTrue(book.path.exists())
        self.assertEqual(library.notes_path_for(self.root / "B" / "x.epub").read_text(), "other")

    def test_rename_book(self):
        book = self.book("A/x.epub", "A")
        book.notes_path.write_text("n")
        library.set_pinned(book, True)
        renamed = library.rename_book(book, "Neuer Name")
        self.assertEqual(renamed.path.name, "Neuer Name.epub")
        self.assertTrue(renamed.notes_path.exists())
        self.assertEqual(library.read_pins(self.root / "A"), ["Neuer Name.epub"])

    def test_rename_book_refuses_existing_and_bad_names(self):
        book = self.book("x.epub")
        self.book("y.epub")
        with self.assertRaises(LibraryError):
            library.rename_book(book, "y")
        with self.assertRaises(LibraryError):
            library.rename_book(book, "../z")
        self.assertTrue(book.path.exists())

    def test_add_book_copies_and_never_overwrites(self):
        source = Path(self.tmp.name) / "neu.epub"
        source.write_bytes(b"new")
        (self.root / "A").mkdir()
        added = library.add_book(source, self.root / "A")
        self.assertEqual(added.path.read_bytes(), b"new")
        self.assertTrue(source.exists())
        source.write_bytes(b"changed")
        with self.assertRaises(LibraryError):
            library.add_book(source, self.root / "A")
        self.assertEqual(added.path.read_bytes(), b"new")
        self.assertEqual([p.name for p in (self.root / "A").iterdir()], ["neu.epub"])

    def test_pins_survive_broken_or_foreign_files(self):
        folder = self.root / "A"
        folder.mkdir()
        (folder / library.SHELF_FILE).write_text("{ broken")
        self.assertEqual(library.read_pins(folder), [])
        (folder / library.SHELF_FILE).write_text('{"pinned": ["a.epub", 3], "future": true}')
        self.assertEqual(library.read_pins(folder), ["a.epub"])
        book = Book(folder / "b.epub", folder)
        library.set_pinned(book, True)
        data = json.loads((folder / library.SHELF_FILE).read_text())
        self.assertEqual(data["pinned"], ["a.epub", "b.epub"])
        self.assertTrue(data["future"], "settings of a newer version must survive")

    def test_trash_includes_notes(self):
        book = self.book("x.epub")
        self.assertEqual(library.trash_paths_for_book(book), [book.path])
        book.notes_path.write_text("n")
        self.assertEqual(library.trash_paths_for_book(book), [book.path, book.notes_path])


if __name__ == "__main__":
    unittest.main()
