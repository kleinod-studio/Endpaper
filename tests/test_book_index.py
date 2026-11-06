import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from endpaper import book_index  # noqa: E402
from endpaper.book_index import Index, read_passages  # noqa: E402
from make_epub import make_epub  # noqa: E402


def passages_of(markup: str):
    reader = book_index._Reader(0)
    reader.feed(markup)
    return [(passage.path, passage.text) for passage in reader.finish()]


class ReadingTest(unittest.TestCase):
    def test_passages_know_their_section_and_element(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = make_epub(Path(tmp) / "b.epub", chapters=3, paragraphs=5)
            passages = read_passages(book)
        self.assertEqual({p.section for p in passages}, {0, 1, 2})
        second = [p for p in passages if p.section == 1]
        self.assertEqual((second[0].path, second[0].text), ([1, 0], "Chapter 2"))
        self.assertEqual(second[1].path, [1, 1])
        self.assertTrue(second[2].text.startswith("0 Sentence number 0 of the chapter called Chapter 2"))
        self.assertEqual(second[2].path, [1, 2])

    def test_only_the_running_text_is_read(self):
        found = passages_of(
            '<?xml version="1.0"?><html xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Title</title>'
            "<style>p { color: red }</style></head><body>"
            "<p>One <em>two</em>&nbsp;three&#173;four.<br/>Next line</p>"
            '<aside epub:type="footnote"><p>A footnote</p></aside>'
            "<div>Before<p>inside</p>after</div><script>alert(1)</script></body></html>"
        )
        self.assertEqual(found, [
            ([1, 0], "One two threefour. Next line"),
            ([1, 2], "Before after"),
            ([1, 2, 0], "inside"),
        ])

    def test_broken_markup_is_read_as_far_as_it_goes(self):
        found = passages_of("<html><body><p>Open <b>bold</i> text</p></span><p>Second")
        self.assertEqual([text for _path, text in found], ["Open bold text", "Second"])


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "cache" / "search.db"
        self.one = make_epub(self.dir / "one.epub", title="One", identifier="urn:one", chapters=2, paragraphs=3)
        self.two = make_epub(self.dir / "two.epub", title="Two", identifier="urn:two", chapters=1, paragraphs=1)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, books):
        index = Index(self.file, writable=True)
        reports = []
        index.update(books, lambda done, total: reports.append((done, total)))
        index.close()
        return reports

    def search(self, query, books=None):
        index = Index(self.file)
        try:
            return index.search(query, books)
        finally:
            index.close()

    def test_finds_books_with_the_most_passages_first(self):
        self.assertEqual(self.build([self.one, self.two]), [(0, 2), (1, 2), (2, 2)])
        found = self.search("sentence number")
        self.assertEqual([(Path(f.book).name, f.count) for f in found], [("one.epub", 6), ("two.epub", 1)])
        first = found[0].passages[0]
        self.assertEqual((first["section"], first["path"]), (0, [1, 2]))
        self.assertIn({"text": "Sentence number", "word": True}, first["parts"])
        self.assertEqual(len(found[0].passages), book_index.EXCERPTS_PER_BOOK)

    def test_case_accents_and_word_beginnings_do_not_matter(self):
        self.build([self.one])
        self.assertEqual(len(self.search("SÉNTENCE")), 1)
        self.assertEqual(len(self.search("chap")), 1)
        self.assertEqual(self.search("entence"), [])
        self.assertEqual(self.search('"*) OR'), [])
        self.assertEqual(self.search(""), [])

    def test_can_be_limited_to_some_books(self):
        self.build([self.one, self.two])
        self.assertEqual([f.book for f in self.search("sentence", [str(self.two)])], [str(self.two)])

    def test_only_new_and_changed_books_are_read_again(self):
        self.build([self.one, self.two])
        self.assertEqual(self.build([self.one, self.two]), [(0, 0)])
        make_epub(self.two, title="Two", identifier="urn:two", chapters=1, paragraphs=4)
        os.utime(self.two, ns=(1, 1))
        self.assertEqual(self.build([self.one, self.two]), [(0, 1), (1, 1)])
        self.assertEqual([f.count for f in self.search("sentence", [str(self.two)])], [4])

    def test_a_moved_book_keeps_its_passages_and_a_gone_one_is_forgotten(self):
        self.build([self.one, self.two])
        moved = self.dir / "category" / "one.epub"
        moved.parent.mkdir()
        self.one.rename(moved)
        self.assertEqual(self.build([moved]), [(0, 0)])
        self.assertEqual([f.book for f in self.search("sentence")], [str(moved)])
        db = sqlite3.connect(self.file)
        self.assertEqual(db.execute("SELECT count(*) FROM places").fetchone()[0], 2 * (3 + 2))
        db.close()

    def test_an_unreadable_book_is_not_tried_again(self):
        broken = self.dir / "broken.epub"
        broken.write_bytes(b"not a zip at all")
        self.build([broken])
        index = Index(self.file)
        self.assertEqual(index.state(str(broken)), "failed")
        index.close()
        self.assertEqual(self.build([broken]), [(0, 0)])

    def test_a_cache_of_another_version_is_built_anew(self):
        self.file.parent.mkdir()
        db = sqlite3.connect(self.file)
        db.execute("CREATE TABLE something (x)")
        db.execute("PRAGMA user_version = 99")
        db.commit()
        db.close()
        self.build([self.two])
        self.assertEqual(len(self.search("sentence")), 1)

    def test_runs_as_a_process_of_its_own(self):
        result = subprocess.run(
            [sys.executable, "-m", "endpaper.book_index", str(self.file)],
            input=f"{self.one}\n{self.two}\n", capture_output=True, text=True, cwd=ROOT / "src", check=True,
        )
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[-1], {"done": 2, "total": 2})
        self.assertEqual(len(self.search("sentence")), 2)


if __name__ == "__main__":
    unittest.main()
