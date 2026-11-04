import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import gi  # noqa: E402

try:
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    from endpaper import book_info  # noqa: E402
except ValueError:
    book_info = None  # CI without GTK: covered by the GUI test

from make_epub import PNG, _chunk  # noqa: E402


@unittest.skipIf(book_info is None, "needs GTK")
class Thumbnails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_png_size_from_header(self):
        self.assertEqual(book_info.image_size(PNG), (2, 3))

    def test_jpeg_size_from_header(self):
        data = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x11\x08" + struct.pack(">HH", 1800, 1172) + b"\x03" + b"\0" * 20
        self.assertEqual(book_info.image_size(data), (1172, 1800))

    def test_unknown_format_has_no_size(self):
        self.assertIsNone(book_info.image_size(b"not an image"))

    def test_thumbnail_is_scaled_to_height(self):
        target = self.dir / "t.png"
        self.assertEqual(book_info.make_thumbnail(PNG, target), str(target))
        self.assertEqual(book_info.image_size(target.read_bytes()), (200, 300))

    def test_huge_image_is_refused_before_decoding(self):
        bomb = (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"\0" * 100))
            + _chunk(b"IEND", b"")
        )
        self.assertIsNone(book_info.make_thumbnail(bomb, self.dir / "t.png"))
        self.assertFalse((self.dir / "t.png").exists())

    def test_broken_image_gives_no_thumbnail(self):
        self.assertIsNone(book_info.make_thumbnail(b"\x89PNG\r\n\x1a\nbroken", self.dir / "t.png"))


if __name__ == "__main__":
    unittest.main()
