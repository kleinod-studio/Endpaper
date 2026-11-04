import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from endpaper import epub  # noqa: E402
from make_epub import PNG, make_epub  # noqa: E402


class ReadInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_metadata_and_cover(self):
        path = make_epub(self.dir / "a.epub", title="Mein Buch", author="Ä. Autor")
        info = epub.read_info(path)
        self.assertEqual(info.title, "Mein Buch")
        self.assertEqual(info.authors, ["Ä. Autor"])
        self.assertEqual(info.language, "en")
        self.assertEqual(epub.read_cover(path, info.cover_path), PNG)

    def test_no_cover_no_author(self):
        info = epub.read_info(make_epub(self.dir / "a.epub", cover=False, author=""))
        self.assertIsNone(info.cover_path)
        self.assertEqual(info.authors, [])

    def test_not_a_zip(self):
        path = self.dir / "a.epub"
        path.write_bytes(b"this is no book")
        with self.assertRaises(epub.EpubError):
            epub.read_info(path)

    def test_missing_container(self):
        path = self.dir / "a.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("hello.txt", "x")
        with self.assertRaises(epub.EpubError):
            epub.read_info(path)

    def test_entity_declarations_are_refused(self):
        path = self.dir / "a.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
        with self.assertRaises(epub.EpubError):
            epub.read_info(path)

    def test_oversized_entry_is_refused(self):
        path = self.dir / "a.epub"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("META-INF/container.xml", b"x" * (epub.MAX_XML_BYTES + 1))
        with self.assertRaises(epub.EpubError):
            epub.read_info(path)

    def test_cover_outside_the_book_is_ignored(self):
        path = self.dir / "a.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="p.opf"/></rootfiles></container>',
            )
            z.writestr(
                "p.opf",
                '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata/>'
                '<manifest><item id="c" href="../../etc/passwd" properties="cover-image"/></manifest></package>',
            )
        self.assertIsNone(epub.read_info(path).cover_path)

    def test_key_follows_the_book_not_the_file(self):
        first = epub.read_info(make_epub(self.dir / "a.epub"))
        second = epub.read_info(make_epub(self.dir / "b.epub"))
        self.assertEqual(epub.book_key(first, "a.epub"), epub.book_key(second, "b.epub"))
        other = epub.read_info(make_epub(self.dir / "c.epub", identifier="urn:test:2"))
        self.assertNotEqual(epub.book_key(first, "a.epub"), epub.book_key(other, "c.epub"))


if __name__ == "__main__":
    unittest.main()
