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
from endpaper import markdown_style  # noqa: E402
from endpaper import notes  # noqa: E402
from endpaper.library import Book  # noqa: E402
from endpaper.notes import Entry, Note, NotesError, NotesFile, NotesKept, parse, serialize  # noqa: E402

CFI = "epubcfi(/6/4!/4/2,/1:0,/1:10)"


def texts(entry):
    return [note.text for note in entry.notes]


class FormatTest(unittest.TestCase):
    def test_the_file_is_plain_markdown(self):
        blocks = [
            notes.Heading(1, "A Book"),
            notes.Heading(2, "Chapter 1"),
            Entry("a1", CFI, "A quote\nover two lines", [Note("n1", "My **note**"), Note("n2", "# Another\n\n- item")], "Chapter 1"),
        ]
        self.assertEqual(
            serialize(blocks),
            "# A Book\n\n## Chapter 1\n\n> A quote\n> over two lines\n\n"
            "<!-- endpaper-note id=n1 -->\nMy **note**\n<!-- endpaper-note-end -->\n\n"
            "<!-- endpaper-note id=n2 -->\n# Another\n\n- item\n<!-- endpaper-note-end -->\n\n"
            f"<!-- endpaper: id=a1 cfi={CFI} color=yellow style=highlight -->\n",
        )

    def test_reading_gives_back_what_was_written(self):
        entry = Entry("a1", CFI, "Quote", [Note("n1", "First\n\nSecond paragraph"), Note("n2", "Other", True)], "Chapter 1", "green", "underline")
        blocks = parse(serialize([notes.Heading(2, "Chapter 1"), entry]))
        self.assertEqual(blocks[1], entry)

    def test_markdown_in_a_note_stays_as_it_is(self):
        for text in (
            "# A heading",
            "## Chapter 1",
            "> a quote",
            "**bold** and *italic*",
            "- list\n- list",
            "normal\n\n## a heading\n\n> a quote\nend",
            "\\# with a backslash",
            "```\ncode\n```",
        ):
            entry = Entry("a1", CFI, "Quote", [Note("n1", text)], None)
            saved = serialize([entry])
            self.assertIn(text, saved, "no escaping, also Typecase has to read it")
            self.assertEqual(parse(saved), [entry], text)

    def test_notes_cannot_break_the_structure(self):
        for text in (
            "<!-- endpaper: id=x cfi=y -->",
            "   <!-- endpaper: id=x cfi=y -->",
            "<!-- endpaper-note-end -->",
            "<!-- endpaper-note id=x -->",
            "\\<!-- endpaper: id=x cfi=y -->",
            "\\\\<!-- endpaper-note-end -->\nmore",
        ):
            entry = Entry("a1", CFI, "Quote", [Note("n1", text)])
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
        text = f"\ufeff# Book\r\n\r\n> Quote\r\n\r\nNote\r\n\r\n<!-- endpaper: id=a1 cfi={CFI} -->\r\n"
        blocks = parse(text)
        self.assertEqual(blocks[0], notes.Heading(1, "Book"))
        self.assertEqual((blocks[1].quote, texts(blocks[1])), ("Quote", ["Note"]))


class OlderFilesTest(unittest.TestCase):
    OLD = f"> Quote\n\nMy note\n\n\\# escaped hash\n\n\\> escaped quote\n\n<!-- endpaper: id=a1 cfi={CFI} color=green style=highlight -->\n"

    def test_a_note_in_the_old_form_is_read(self):
        entry = parse(self.OLD)[0]
        self.assertEqual(texts(entry), ["My note\n\n# escaped hash\n\n> escaped quote"])
        self.assertEqual(entry.color, "green")

    def test_its_id_stays_the_same_until_it_is_saved(self):
        self.assertEqual(parse(self.OLD)[0].notes[0].id, parse(self.OLD)[0].notes[0].id)

    def test_it_takes_the_new_form_when_saved(self):
        saved = serialize(parse(self.OLD))
        self.assertIn("<!-- endpaper-note id=", saved)
        self.assertIn("# escaped hash", saved)
        self.assertEqual(texts(parse(saved)[0]), texts(parse(self.OLD)[0]))
        self.assertEqual(parse(saved)[0].notes[0].id, parse(self.OLD)[0].notes[0].id)

    def test_changing_a_note_of_an_old_file_works(self):
        with tempfile.TemporaryDirectory() as folder:
            book = Path(folder) / "b.epub"
            book.write_bytes(b"x")
            (Path(folder) / "b.notes.md").write_text(self.OLD, encoding="utf-8")
            file = NotesFile(book)
            file.update_note("a1", file.entries[0].notes[0].id, "changed")
            self.assertEqual(texts(NotesFile(book).entries[0]), ["changed"])


class HandEditingTest(unittest.TestCase):
    def test_changed_text_is_taken_over(self):
        text = f"> Quote\n\nChanged by hand\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        self.assertEqual(texts(parse(text)[0]), ["Changed by hand"])

    def test_changed_text_in_a_note_is_taken_over(self):
        text = f"> Quote\n\n<!-- endpaper-note id=n1 -->\nBy **hand**\n<!-- endpaper-note-end -->\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        self.assertEqual(parse(text)[0].notes, [Note("n1", "By **hand**")])

    def test_text_written_beside_a_note_becomes_a_note(self):
        text = f"> Quote\n\nFirst\n\n<!-- endpaper-note id=n1 -->\nSecond\n<!-- endpaper-note-end -->\n\nThird\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        self.assertEqual(texts(parse(text)[0]), ["First", "Second", "Third"])

    def test_a_missing_end_marker_loses_nothing(self):
        text = f"> Quote\n\n<!-- endpaper-note id=n1 -->\nA note\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n\n## Next\n\n<!-- endpaper: id=a2 cfi=c2 -->\n"
        blocks = parse(text)
        self.assertEqual(texts(blocks[0]), ["A note"])
        self.assertEqual(blocks[1], notes.Heading(2, "Next"))
        self.assertEqual(blocks[2].id, "a2")

    def test_a_note_whose_mark_lost_its_anchor_stays_as_text(self):
        text = "> Quote\n\n<!-- endpaper-note id=n1 -->\nImportant\n<!-- endpaper-note-end -->\n"
        blocks = parse(text)
        self.assertEqual(notes._entries(blocks), [])
        self.assertIn("Important", serialize(blocks))
        self.assertEqual(serialize(parse(serialize(blocks))), serialize(blocks))

    def test_doubled_note_ids_keep_both_texts(self):
        block = "<!-- endpaper-note id=n1 -->\n{}\n<!-- endpaper-note-end -->\n\n"
        entry = parse(block.format("One") + block.format("Two") + f"<!-- endpaper: id=a1 cfi={CFI} -->\n")[0]
        self.assertEqual(texts(entry), ["One", "Two"])
        self.assertEqual(len({n.id for n in entry.notes}), 2)

    def test_a_blockquote_inside_a_note_stays_in_the_note(self):
        text = f"> Quote\n\nMine\n\n> Another\n\nEnd\n\n<!-- endpaper: id=a1 cfi={CFI} -->\n"
        entry = parse(text)[0]
        self.assertEqual(entry.quote, "Quote")
        self.assertIn("> Another", texts(entry)[0])
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
        shapes = ["word{n}", "> word{n}", ">word{n}", "# word{n}", "## word{n}", "\\# word{n}", "    word{n}", "- word{n}", "```word{n}", "<!-- endpaper-note id=x -->", "<!-- endpaper-note-end -->", "", ""]
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
        file.update(entry.id, color="green")
        note = file.add_note(entry.id, "Note")
        self.assertEqual((texts(self.open().entries[0]), self.open().entries[0].color), (["Note"], "green"))
        file.update_note(entry.id, note.id, "Changed")
        self.assertEqual(texts(self.open().entries[0]), ["Changed"])
        file.remove(entry.id)
        self.assertEqual(self.open().entries, [])
        self.assertTrue(self.path.exists(), "the file is not deleted, it may hold text of the reader")
        with self.assertRaises(KeyError):
            file.remove("missing")

    def test_a_mark_can_have_many_notes(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        self.assertFalse(entry.has_note)
        first = file.add_note(entry.id, "First **note**")
        second = file.add_note(entry.id, "# Second\n\n- with a list")
        third = file.add_note(entry.id, "Third")
        self.assertEqual(texts(self.open().entries[0]), ["First **note**", "# Second\n\n- with a list", "Third"])
        self.assertTrue(self.open().entries[0].has_note)
        file.remove_note(entry.id, second.id)
        self.assertEqual(texts(self.open().entries[0]), ["First **note**", "Third"])
        self.assertEqual({first.id, third.id}, {n.id for n in self.open().entries[0].notes})
        with self.assertRaises(KeyError):
            file.remove_note(entry.id, "missing")

    def test_a_note_has_no_limit(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        long = "\n\n".join(f"Paragraph {n} " + "word " * 200 for n in range(500))
        file.add_note(entry.id, long.strip())
        self.assertEqual(texts(self.open().entries[0]), ["\n\n".join(f"Paragraph {n} " + "word " * 200 for n in range(500)).strip()])

    def test_undo_takes_back_a_new_mark_and_redo_brings_it_back(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        self.assertTrue(file.undo())
        self.assertEqual(self.open().entries, [])
        self.assertTrue(file.redo())
        self.assertEqual([e.id for e in self.open().entries], [entry.id])
        self.assertFalse(file.redo())

    def test_undo_brings_a_removed_mark_back_with_its_notes_and_place(self):
        file = self.open()
        first = file.add("c1", "One", chapter="A")
        second = file.add("c2", "Two", "My note", chapter="A")
        third = file.add("c3", "Three", chapter="A")
        file.add_note(second.id, "Another note")
        file.remove(second.id)
        self.assertEqual([e.id for e in self.open().entries], [first.id, third.id])
        file.undo()
        saved = self.open().entries
        self.assertEqual([e.id for e in saved], [first.id, second.id, third.id])
        self.assertEqual(texts(saved[1]), ["My note", "Another note"])
        file.redo()
        self.assertEqual([e.id for e in self.open().entries], [first.id, third.id])

    def test_undo_restores_color_and_style(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        file.update(entry.id, color="green")
        file.update(entry.id, style="underline")
        file.undo()
        self.assertEqual((self.open().entries[0].color, self.open().entries[0].style), ("green", "highlight"))
        file.undo()
        self.assertEqual(self.open().entries[0].color, "yellow")
        file.redo()
        file.redo()
        self.assertEqual((self.open().entries[0].color, self.open().entries[0].style), ("green", "underline"))

    def test_undo_brings_a_removed_note_back_in_its_place(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        file.add_note(entry.id, "First")
        second = file.add_note(entry.id, "Second")
        file.add_note(entry.id, "Third")
        file.remove_note(entry.id, second.id, undoable=True)
        self.assertEqual(texts(self.open().entries[0]), ["First", "Third"])
        file.undo()
        self.assertEqual(texts(self.open().entries[0]), ["First", "Second", "Third"])
        file.redo()
        self.assertEqual(texts(self.open().entries[0]), ["First", "Third"])

    def test_undo_never_takes_away_a_note_written_since(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        note = file.add_note(entry.id, "Typed")
        file.update_note(entry.id, note.id, "Typed more")
        with self.assertRaises(NotesKept):
            file.undo()
        self.assertEqual(texts(self.open().entries[0]), ["Typed more"])
        self.assertFalse(file.undo(), "that step is used up")

    def test_redo_never_takes_away_a_note_written_since(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "Note")
        file.remove(entry.id)
        file.undo()
        file.add_note(entry.id, "Written after the undo")
        with self.assertRaises(NotesKept):
            file.redo()
        self.assertEqual(texts(self.open().entries[0]), ["Note", "Written after the undo"])

    def test_redo_never_takes_away_a_note_changed_since(self):
        file = self.open()
        entry = file.add(CFI, "Quote")
        note = file.add_note(entry.id, "Note")
        file.remove_note(entry.id, note.id, undoable=True)
        file.undo()
        file.update_note(entry.id, note.id, "Note, changed")
        with self.assertRaises(NotesKept):
            file.redo()
        self.assertEqual(texts(self.open().entries[0]), ["Note, changed"])

    def test_a_new_change_ends_redo(self):
        file = self.open()
        file.add("c1")
        file.undo()
        file.add("c2")
        self.assertFalse(file.redo())
        self.assertEqual([e.cfi for e in self.open().entries], ["c2"])

    def test_steps_are_undone_in_reverse_order(self):
        file = self.open()
        for number in range(5):
            file.add(f"c{number}")
        for expected in (4, 3, 2, 1, 0):
            self.assertEqual(len(self.open().entries), expected + 1)
            file.undo()
        self.assertEqual(self.open().entries, [])
        self.assertFalse(file.undo())

    def test_undo_keeps_what_changed_elsewhere(self):
        file = self.open()
        file.add(CFI, "Quote", "base")
        self.outside(lambda text: text.replace("base", "changed outside"))
        with self.assertRaises(NotesKept):
            file.undo()
        self.assertEqual(texts(self.open().entries[0]), ["changed outside"])

    def test_undo_skips_what_is_gone(self):
        file = self.open()
        first = file.add("c1", "One")
        file.update(first.id, color="green")
        second = file.add("c2", "Two")
        file.update(second.id, color="blue")
        self.path.write_text(serialize([notes.Heading(1, "A Book"), first]), encoding="utf-8")
        file.refresh()
        self.assertTrue(file.undo(), "what is gone cannot be undone: on to the step before")
        self.assertEqual([(e.id, e.color) for e in self.open().entries], [(first.id, "yellow")])

    def test_undo_is_saved_atomically_like_everything_else(self):
        file = self.open()
        file.add("c1", text="safe")
        file.add("c2", text="second")
        before = self.path.read_bytes()
        with mock.patch("endpaper.fileio.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(NotesError):
                file.undo()
        self.assertEqual(self.path.read_bytes(), before)
        file.flush()
        self.assertEqual([e.cfi for e in self.open().entries], ["c1"])

    def test_a_failed_save_leaves_the_file_and_keeps_the_change(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        with mock.patch("endpaper.fileio.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(NotesError):
                file.add("c2", text="not yet on disk")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["book.epub", "book.notes.md"], "no leftovers")
        self.assertEqual([e.notes[0].text for e in file.entries], ["safe", "not yet on disk"])
        file.flush()
        self.assertEqual([e.notes[0].text for e in self.open().entries], ["safe", "not yet on disk"])

    def test_a_crash_while_writing_leaves_the_old_file(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        with mock.patch("endpaper.fileio.os.fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                file.add("c2", text="lost in the crash")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([e.notes[0].text for e in self.open().entries], ["safe"])

    def test_a_file_that_cannot_be_read_is_not_written_over(self):
        file = self.open()
        file.add("c1", text="safe")
        before = self.path.read_bytes()
        real_read = Path.read_bytes
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("no")):
            with self.assertRaises(NotesError):
                file.add("c2", text="waits")
        self.assertEqual(real_read(self.path), before)
        self.assertEqual([e.notes[0].text for e in file.entries], ["safe", "waits"], "still shown while it waits")
        file.flush()
        self.assertEqual([e.notes[0].text for e in self.open().entries], ["safe", "waits"])

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
        self.assertEqual(texts(file.entries[0]), ["new"])

    def test_marks_added_on_both_sides_stay(self):
        file = self.open()
        file.add("c1", text="base")
        other = self.open()
        other.add("c2", text="other computer")
        file.add("c3", text="this computer")
        self.assertEqual([e.notes[0].text for e in self.open().entries], ["base", "other computer", "this computer"])

    def test_different_changes_of_one_mark_are_merged(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "changed outside"))
        file.update(entry.id, color="green")
        saved = self.open().entries
        self.assertEqual([(texts(e), e.color) for e in saved], [(["changed outside"], "green")])

    def test_the_same_note_changed_on_both_sides_keeps_both(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        self.outside(lambda text: text.replace("base", "outside"))
        file.update_note(entry.id, entry.notes[0].id, "here")
        saved = self.open().entries
        self.assertEqual(len(saved), 1)
        self.assertEqual(texts(saved[0]), ["outside", "here"])
        self.assertEqual([n.conflict for n in saved[0].notes], [False, True])

    def test_the_same_change_on_both_sides_is_no_conflict(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "same"))
        file.update_note(entry.id, entry.notes[0].id, "same")
        self.assertEqual(texts(self.open().entries[0]), ["same"])

    def test_notes_added_on_both_sides_stay(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        other = self.open()
        other.add_note(entry.id, "other computer")
        file.add_note(entry.id, "this computer")
        self.assertEqual(texts(self.open().entries[0]), ["base", "other computer", "this computer"])

    def test_removing_a_note_that_changed_elsewhere_keeps_it(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        self.outside(lambda text: text.replace("base", "important addition"))
        file.remove_note(entry.id, entry.notes[0].id)
        self.assertEqual(texts(self.open().entries[0]), ["important addition"])

    def test_removing_what_was_changed_elsewhere_keeps_it(self):
        file = self.open()
        entry = file.add(CFI, text="base")
        self.outside(lambda text: text.replace("base", "important addition"))
        file.remove(entry.id)
        self.assertEqual(texts(self.open().entries[0]), ["important addition"])

    def test_a_note_for_what_was_removed_elsewhere_keeps_it(self):
        file = self.open()
        keep = file.add("c1", text="other")
        entry = file.add("c2", "Quote", "base")
        self.path.write_text(serialize([notes.Heading(1, "A Book"), keep]), encoding="utf-8")
        file.add_note(entry.id, "still wanted")
        self.assertEqual([texts(e) for e in self.open().entries], [["other"], ["base", "still wanted"]])

    def test_changing_a_note_of_what_was_removed_elsewhere_keeps_it(self):
        file = self.open()
        keep = file.add("c1", text="other")
        entry = file.add("c2", "Quote", "base")
        self.path.write_text(serialize([notes.Heading(1, "A Book"), keep]), encoding="utf-8")
        file.update_note(entry.id, entry.notes[0].id, "still wanted")
        self.assertEqual([texts(e) for e in self.open().entries], [["other"], ["still wanted"]])

    def test_the_file_deleted_outside_does_not_lose_the_next_change(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "base")
        self.path.unlink()
        file.update_note(entry.id, entry.notes[0].id, "still wanted")
        self.assertEqual(texts(self.open().entries[0]), ["still wanted"])

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


class AnswersTest(unittest.TestCase):
    KEY = "one.xhtml#q1"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.book = Path(self.folder.name) / "book.epub"
        self.book.write_bytes(b"not touched")
        self.path = self.book.with_name("book.notes.md")

    def open(self):
        return NotesFile(self.book, "A Book")

    def test_an_answer_is_readable_and_comes_back(self):
        file = self.open()
        file.set_answer(self.KEY, "My answer\n\nin two parts", "The question?", "Chapter 1")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("> The question?", text)
        self.assertIn("My answer\n\nin two parts", text)
        self.assertIn("## Chapter 1", text)
        answer = self.open().answers[0]
        self.assertEqual((answer.field, answer.text, answer.question, answer.chapter), (self.KEY, "My answer\n\nin two parts", "The question?", "Chapter 1"))
        self.assertEqual(self.book.read_bytes(), b"not touched")

    def test_answers_and_marks_live_side_by_side(self):
        file = self.open()
        entry = file.add(CFI, "Quote", "Note", "Chapter 1")
        file.set_answer(self.KEY, "Answer", "Question?", "Chapter 1")
        file.add("c2", "Second", "", "Chapter 1")
        again = self.open()
        self.assertEqual([e.cfi for e in again.entries], [CFI, "c2"])
        self.assertEqual(again.entries[0].id, entry.id)
        self.assertEqual(again.answers[0].text, "Answer")
        self.assertEqual(again.answers[0].question, "Question?")
        self.assertEqual(again.loose, [])

    def test_saving_again_changes_nothing(self):
        file = self.open()
        file.set_answer(self.KEY, "Answer", "Question?", "Chapter 1")
        file.set_answer("one.xhtml#tick", "[x]", "Read it", "Chapter 1", "check")
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(serialize(parse(text)), text)

    def test_any_text_is_safe_in_an_answer(self):
        tricky = "<!-- endpaper: id=abc cfi=x -->\n<!-- endpaper-answer-end -->\n# Heading\n> Quote\n<!-- endpaper-note id=n -->"
        file = self.open()
        file.set_answer(self.KEY, tricky)
        again = self.open()
        self.assertEqual(again.answers[0].text, tricky)
        self.assertEqual(again.entries, [])

    def test_emptying_a_field_takes_the_answer_away(self):
        file = self.open()
        file.add(CFI, "Quote")
        file.set_answer(self.KEY, "Answer")
        file.set_answer(self.KEY, "")
        self.assertEqual(self.open().answers, [])

    def test_a_change_elsewhere_stays_next_to_the_one_here(self):
        file = self.open()
        file.set_answer(self.KEY, "Base")
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("Base", "Changed outside"), encoding="utf-8")
        file.set_answer(self.KEY, "Changed here")
        text = self.open().answers[0].text
        self.assertIn("Changed outside", text)
        self.assertIn("Changed here", text)

    def test_emptying_a_field_keeps_a_change_made_elsewhere(self):
        file = self.open()
        file.set_answer(self.KEY, "Base")
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("Base", "Changed outside"), encoding="utf-8")
        file.set_answer(self.KEY, "")
        self.assertEqual(self.open().answers[0].text, "Changed outside")

    def test_a_change_elsewhere_arrives(self):
        file = self.open()
        file.set_answer(self.KEY, "Base")
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("Base", "Edited by hand"), encoding="utf-8")
        self.assertTrue(file.refresh())
        self.assertEqual(file.answers[0].text, "Edited by hand")

    def test_a_second_answer_for_the_same_field_stays_as_text(self):
        block = "<!-- endpaper-answer field=one.xhtml%23q1 kind=text -->\n{}\n<!-- endpaper-answer-end -->"
        self.path.write_text(block.format("First") + "\n\n" + block.format("Second") + "\n", encoding="utf-8")
        file = self.open()
        self.assertEqual([a.text for a in file.answers], ["First"])
        self.assertIn("Second", file.loose[0])
        file.add(CFI, "Quote")
        self.assertIn("Second", self.path.read_text(encoding="utf-8"))

    def test_a_missing_end_marker_loses_nothing(self):
        self.path.write_text("<!-- endpaper-answer field=one.xhtml%23q1 kind=text -->\nStill here\n", encoding="utf-8")
        self.assertEqual(self.open().answers[0].text, "Still here")
        self.path.write_text(
            "<!-- endpaper-answer field=a kind=text -->\nFirst\n\n> Quote\n\n<!-- endpaper: id=abc cfi=c1 -->\n", encoding="utf-8",
        )
        file = self.open()
        self.assertIn("First", file.answers[0].text)
        self.assertEqual(file.entries[0].cfi, "c1")

    def test_an_answer_without_a_field_stays_as_text(self):
        self.path.write_text("<!-- endpaper-answer kind=text -->\nOrphan\n<!-- endpaper-answer-end -->\n", encoding="utf-8")
        file = self.open()
        self.assertEqual(file.answers, [])
        self.assertIn("Orphan", file.loose[0])

    def test_a_failed_save_keeps_the_answer(self):
        file = self.open()
        file.set_answer("first", "safe")
        with mock.patch("endpaper.fileio.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(NotesError):
                file.set_answer(self.KEY, "Waiting")
        self.assertEqual(self.open().answers[0].text, "safe")
        file.flush()
        self.assertEqual({a.field: a.text for a in self.open().answers}, {"first": "safe", self.KEY: "Waiting"})


class MarkdownStyleTest(unittest.TestCase):
    def test_an_image_in_a_note_is_recognized(self):
        spans = markdown_style.line_spans("![A caption](picture.png) and text", False)
        self.assertEqual(spans[0], (0, 2, "syntax"))


if __name__ == "__main__":
    unittest.main()
