import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from endpaper import library  # noqa: E402
from endpaper import notes  # noqa: E402
from endpaper.library import Book  # noqa: E402
from endpaper.notes import Entry, NotesError, NotesFile, parse, serialize  # noqa: E402

CFI = "epubcfi(/6/4!/4/2,/1:0,/1:10)"


class FormatTest(unittest.TestCase):
    def test_the_file_is_plain_markdown(self):
        blocks = [
            notes.Heading(1, "A Book"),
            notes.Heading(2, "Chapter 1"),
            Entry("a1", CFI, "A quote\nover two lines", "My note", "Chapter 1"),
        ]
        self.assertEqual(
            serialize(blocks),
            "# A Book\n\n## Chapter 1\n\n> A quote\n> over two lines\n\nMy note\n\n"
            f"<!-- endpaper: id=a1 cfi={CFI} color=yellow style=highlight -->\n",
        )

    def test_reading_gives_back_what_was_written(self):
        entry = Entry("a1", CFI, "Quote", "First\n\nSecond paragraph", "Chapter 1", "green", "underline")
        blocks = parse(serialize([notes.Heading(2, "Chapter 1"), entry]))
        self.assertEqual(blocks[1], entry)

    def test_notes_cannot_break_the_structure(self):
        for text in (
            "# Not a heading",
            "> not a quote",
            "<!-- endpaper: id=x cfi=y -->",
            "\\# with a backslash",
            "\\\\> two backslashes",
            "   <!-- endpaper: id=x cfi=y -->",
            "normal\n\n## also not a heading\n\n> nor a quote\nend",
        ):
            entry = Entry("a1", CFI, "Quote", text)
            self.assertEqual(parse(serialize([entry])), [entry], text)

    def test_odd_anchors_survive(self):
        for cfi in ("epubcfi(/6/4!/4[a b]/2)", "epubcfi(/6/4!/4[-->]/2)", "epubcfi(/6/4!/4[%41]/2)", "ünï"):
            entry = Entry("a1", cfi)
            self.assertEqual(parse(serialize([entry])), [entry], cfi)

    def test_a_mark_without_quote_or_note(self):
        entry = Entry("a1", CFI)
        self.assertEqual(parse(serialize([entry])), [entry])

    def test_settings_of_a_newer_version_survive(self):
        text = f"<!-- endpaper: id=a1 cfi={CFI} color=pink style=wave future=yes -->\n"
        self.assertEqual(serialize(parse(text)), text)
        self.assertEqual(parse(text)[0].extra, {"future": "yes"})

    def test_windows_line_ends_and_bom(self):
        text = f"﻿# Book\r\n\r\n> Quote\r\n\r\nNote\r\n\r\n<!-- endpaper: id=a1 cfi={CFI} -->\r\n"
        blocks = parse(text)
        self.assertEqual(blocks[0], notes.Heading(1, "Book"))
        self.assertEqual((blocks[1].quote, blocks[1].text), ("Quote", "Note"))


class HandEditingTest(unittest.TestCase):
    def test_changed_text_is_taken_over(self):
        text = f"> Quote\n\nChanged by hand\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        self.assertEqual(parse(text)[0].text, "Changed by hand")

    def test_a_blockquote_inside_a_note_stays_in_the_note(self):
        text = f"> Quote\n\nMine\n\n> Another\n\nEnd\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        entry = parse(text)[0]
        self.assertEqual(entry.quote, "Quote")
        self.assertIn("> Another", entry.text)
        self.assertEqual(parse(serialize([entry]))[0], entry)

    def test_text_outside_marks_stays(self):
        text = f"My own words\n\n> Quote\n\n<!-- endpaper: id=a1 cfi={CFI} color=yellow style=highlight -->\n\nMore words\n"
        blocks = parse(text)
        self.assertEqual(blocks[0], notes.Loose("My own words"))
        self.assertEqual(blocks[2], notes.Loose("More words"))
        self.assertEqual(serialize(blocks), text)

    def test_a_mark_that_lost_its_anchor_stays_as_text(self):
        for comment in ("", "<!-- endpaper: id=a1 -->", "<!-- endpaper: broken -->", f"<!-- endpaper: id=a!1 cfi={CFI} -->"):
            blocks = parse(f"> Quote\n\nImportant\n\n{comment}\n")
            self.assertEqual(notes._entries(blocks), [], comment)
            self.assertIn("Important", serialize(blocks))
            self.assertIn("Quote", serialize(blocks))

    def test_a_doubled_mark_keeps_both_texts(self):
        anchor = f"<!-- endpaper: id=a1 cfi={CFI} -->"
        blocks = parse(f"One\n\n{anchor}\n\nTwo\n\n{anchor}\n")
        self.assertEqual(len(notes._entries(blocks)), 1)
        self.assertIn("Two", serialize(blocks))
        self.assertEqual(serialize(parse(serialize(blocks))), serialize(blocks))

    def test_moving_a_mark_changes_its_chapter(self):
        text = f"## One\n\n## Two\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        self.assertEqual(parse(text)[2].chapter, "Two")

    def test_nothing_is_ever_lost_whatever_the_file_looks_like(self):
        anchors = ["<!-- endpaper: id=a{n} cfi=c -->", "<!-- endpaper: id=a1 cfi=c -->", "<!-- endpaper: oops -->", "<!-- other -->"]
        shapes = ["word{n}", "> word{n}", ">word{n}", "# word{n}", "## word{n}", "\\# word{n}", "    word{n}", "- word{n}", "```word{n}", "", ""]
        random.seed(7)
        for _round in range(300):
            lines, words = [], []
            for n in range(random.randint(0, 25)):
                template = random.choice(shapes + anchors[:2] if random.random() < 0.3 else shapes + anchors[2:])
                lines.append(template.format(n=n))
                if "word" in template:
                    words.append(f"word{n}")
            text = "\n".join(lines)
            once = serialize(parse(text))
            for word in words:
                self.assertRegex(once, rf"\b{word}\b", text)
            self.assertEqual(serialize(parse(once)), once, text)


class NotesFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.book = self.folder / "book.epub"
        self.book.write_bytes(b"not touched")
        self.path = self.folder / "book.notes.md"

    def tearDown(self):
        self.tmp.cleanup()

    def open(self):
        return NotesFile(self.book, "A Book")

    def outside(self, change):
        self.path.write_text(change(self.path.read_text(encoding="utf-8")), encoding="utf-8")

    def test_the_file_appears_with_the_first_mark(self):
        file = self.open()
        self.assertFalse(self.path.exists())
        entry = file.add(CFI, "Quote", "Note", "Chapter 1")
        self.assertIn("# A Book", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.open().entries, [entry])
        self.assertEqual(self.book.read_bytes(), b"not touched")

    def test_marks_are_sorted_into_their_chapters(self):
        file = self.open()
        file.add("c1", text="first", chapter="One")
        file.add("c2", text="second", chapter="Two")
        file.add("c3", text="third", chapter="One")
        file.add("c0", text="no chapter")
        self.assertEqual([e.cfi for e in self.open().entries], ["c0", "c1", "c3", "c2"])

    def test_change_and_remove(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        file.update(entry.id, text="Note", color="green")
        self.assertEqual((self.open().entries[0].text, self.open().entries[0].color), ("Note", "green"))
        file.remove(entry.id)
        self.assertEqual(self.open().entries, [])
        self.assertTrue(self.path.exists(), "the file is not deleted, it may hold text of the reader")
        with self.assertRaises(KeyError):
            file.remove("missing")

    def test_a_failed_save_leaves_the_file_and_keeps_the_change(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        with mock.patch("endpaper.fileio.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(NotesError):
                file.add("c2", text="not yet on disk")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["book.epub", "book.notes.md"], "no leftovers")
        self.assertEqual([e.text for e in file.entries], ["safe", "not yet on disk"])
        file.flush()
        self.assertEqual([e.text for e in self.open().entries], ["safe", "not yet on disk"])

    def test_a_crash_while_writing_leaves_the_old_file(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        with mock.patch("endpaper.fileio.os.fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                file.add("c2", text="lost in the crash")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([e.text for e in self.open().entries], ["safe"])

    def test_a_file_that_cannot_be_read_is_not_written_over(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        real_read = Path.read_bytes
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("no")):
            with self.assertRaises(NotesError):
                file.add("c2", text="waits")
        self.assertEqual(real_read(self.path), before)
        self.assertEqual([e.text for e in file.entries], ["safe", "waits"], "still shown while it waits")
        file.flush()
        self.assertEqual([e.text for e in self.open().entries], ["safe", "waits"])

    def test_a_chapter_title_cannot_break_the_file(self):
        file = self.open()
        file.add("c1", text="note", chapter="Two\nlines  here")
        self.assertEqual(self.open().entries[0].chapter, "Two lines here")
        file.add("c2", text="other", chapter="   ")
        self.assertIsNone(self.open().entries[0].chapter)

    def test_text_written_by_hand_survives_our_saves(self):
        file = self.open()
        file.add(CFI, "A quote", "mine", "One")
        self.outside(lambda text: text + "\nWritten by hand\n")
        file.add("c2", "Another quote", "new", "One")
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("Written by hand", saved)
        self.assertEqual(self.open().loose, ["Written by hand"])

    def test_refresh_sees_changes_from_outside(self):
        file = self.open()
        entry = file.add(CFI, text="old")
        self.assertFalse(file.refresh())
        self.outside(lambda text: text.replace("old", "new"))
        self.assertTrue(file.refresh())
        self.assertEqual(file.entries[0].id, entry.id)
        self.assertEqual(file.entries[0].text, "new")

    def test_marks_added_on_both_sides_stay(self):
        file = self.open()
        file.add("c1", text="base")
        other = self.open()
        other.add("c2", text="other computer")
        file.add("c3", text="this computer")
        self.assertEqual([e.text for e in self.open().entries], ["base", "other computer", "this computer"])

    def test_different_changes_of_one_mark_are_merged(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "changed outside"))
        file.update(entry.id, color="green")
        saved = self.open().entries
        self.assertEqual([(e.text, e.color) for e in saved], [("changed outside", "green")])

    def test_the_same_text_changed_on_both_sides_keeps_both(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        self.outside(lambda text: text.replace("base", "outside"))
        file.update(entry.id, text="here")
        saved = self.open().entries
        self.assertEqual(sorted(e.text for e in saved), ["here", "outside"])
        self.assertEqual([e.text for e in saved if e.conflict], ["here"])
        self.assertEqual(len({e.id for e in saved}), 2)
        self.assertEqual({e.cfi for e in saved}, {CFI})

    def test_the_same_change_on_both_sides_is_no_conflict(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "same"))
        file.update(entry.id, text="same")
        self.assertEqual([e.text for e in self.open().entries], ["same"])

    def test_removing_what_was_changed_elsewhere_keeps_it(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "important addition"))
        file.remove(entry.id)
        self.assertEqual([e.text for e in self.open().entries], ["important addition"])

    def test_changing_what_was_removed_elsewhere_keeps_it(self):
        file = self.open()
        keep = file.add("c1", text="other")
        entry = file.add("c2", "Quote", "base")
        file_text = serialize([notes.Heading(1, "A Book"), keep])
        self.path.write_text(file_text, encoding="utf-8")
        file.update(entry.id, text="still wanted")
        self.assertEqual([e.text for e in self.open().entries], ["other", "still wanted"])

    def test_the_file_deleted_outside_does_not_lose_the_next_change(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        self.path.unlink()
        file.update(entry.id, text="still wanted")
        self.assertEqual([e.text for e in self.open().entries], ["still wanted"])

    def test_a_file_that_is_no_text_stays_untouched(self):
        garbage = b"# Mine\n\xff\xfe\x00 not utf-8"
        self.path.write_bytes(garbage)
        file = self.open()
        self.assertTrue(file.damaged)
        file.add("c1", text="first")
        file.add("c2", text="second")
        self.assertEqual(self.path.read_bytes(), garbage)
        self.assertEqual(library.conflict_paths_for(self.book), [file.conflict_path])
        saved = file.conflict_path.read_text(encoding="utf-8")
        self.assertIn("first", saved)
        self.assertIn("second", saved)

    def test_a_file_damaged_later_does_not_lose_what_was_known(self):
        file = self.open()
        file.add("c1", text="known")
        garbage = b"\xff\xfe garbage"
        self.path.write_bytes(garbage)
        file.add("c2", text="new")
        self.assertEqual(self.path.read_bytes(), garbage)
        saved = file.conflict_path.read_text(encoding="utf-8")
        self.assertIn("known", saved)
        self.assertIn("new", saved)

    def test_a_repaired_file_is_used_again(self):
        self.path.write_bytes(b"\xff\xfe")
        file = self.open()
        file.add("c1", text="in the conflict file")
        self.path.write_text("# Repaired\n", encoding="utf-8")
        file.add("c2", text="in the notes file")
        self.assertFalse(file.damaged)
        self.assertIn("in the notes file", self.path.read_text(encoding="utf-8"))

    def test_a_symlinked_notes_file_stays_a_link(self):
        real = self.folder / "elsewhere.md"
        real.write_text("# Linked\n", encoding="utf-8")
        os.symlink(real, self.path)
        self.open().add(CFI, text="through the link")
        self.assertTrue(self.path.is_symlink())
        self.assertIn("through the link", real.read_text(encoding="utf-8"))

    def test_conflict_files_move_and_go_to_the_trash_with_the_book(self):
        category = self.folder / "Category"
        category.mkdir()
        book = Book(self.book, self.folder)
        self.path.write_text("notes", encoding="utf-8")
        conflict = library.conflict_path_for(self.book, "20260101-120000")
        conflict.write_text("conflict", encoding="utf-8")
        self.assertEqual(library.trash_paths_for_book(book), [self.book, self.path, conflict])
        moved = library.move_book(book, category)
        self.assertEqual(moved.notes_path.read_text(encoding="utf-8"), "notes")
        self.assertEqual([p.read_text(encoding="utf-8") for p in library.conflict_paths_for(moved.path)], ["conflict"])
        self.assertEqual(library.conflict_paths_for(self.book), [])

    def test_a_move_that_would_overwrite_a_conflict_file_stops(self):
        category = self.folder / "Category"
        category.mkdir()
        conflict = library.conflict_path_for(self.book, "1")
        conflict.write_text("mine", encoding="utf-8")
        library.conflict_path_for(category / "book.epub", "1").write_text("other", encoding="utf-8")
        with self.assertRaises(library.LibraryError):
            library.move_book(Book(self.book, self.folder), category)
        self.assertTrue(self.book.exists())
        self.assertEqual(conflict.read_text(encoding="utf-8"), "mine")


if __name__ == "__main__":
    unittest.main()
