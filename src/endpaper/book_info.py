"""Title, author and cover of the books, read in the background and kept in a cache.

The cache can always be thrown away: it holds nothing that is not in the books themselves."""

import hashlib
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib

from . import config, epub
from .fileio import atomic_write_text

THUMBNAIL_HEIGHT = 300
MAX_COVER_PIXELS = 64_000_000
CACHE_VERSION = 2
SAVE_DELAY_MS = 2000


@dataclass
class BookInfo:
    title: str
    authors: list[str] = field(default_factory=list)
    key: str = ""
    language: str = ""
    cover: str | None = None  # path of the thumbnail in the cache
    readable: bool = True

    @property
    def author_text(self) -> str:
        return ", ".join(self.authors)


def _signature(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from the header of a PNG or JPEG, without decoding it. None if unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        position = 2
        while position + 9 < len(data):
            if data[position] != 0xFF:
                return None
            marker = data[position + 1]
            if marker == 0xFF:  # padding
                position += 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame
                height, width = struct.unpack(">HH", data[position + 5 : position + 9])
                return width, height
            position += 2 + struct.unpack(">H", data[position + 2 : position + 4])[0]
    return None


def _decode(data: bytes) -> GdkPixbuf.Pixbuf:
    """GTK's own loaders first: they need no helper process, which matters inside the Flatpak."""
    try:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error:  # not PNG, JPEG or TIFF
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        return GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    pixels, stride = downloader.download_bytes()
    return GdkPixbuf.Pixbuf.new_from_bytes(
        pixels, GdkPixbuf.Colorspace.RGB, True, 8, texture.get_width(), texture.get_height(), stride
    )


def make_thumbnail(data: bytes, target: Path) -> str | None:
    size = image_size(data)
    if size and size[0] * size[1] > MAX_COVER_PIXELS:
        return None  # a tiny file can decode into gigabytes
    try:
        pixbuf = _decode(data)
        width = max(1, round(pixbuf.get_width() * THUMBNAIL_HEIGHT / pixbuf.get_height()))
        scaled = pixbuf.scale_simple(width, THUMBNAIL_HEIGHT, GdkPixbuf.InterpType.BILINEAR)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        memory_format = Gdk.MemoryFormat.R8G8B8A8 if scaled.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
        texture = Gdk.MemoryTexture.new(
            scaled.get_width(), scaled.get_height(), memory_format, scaled.read_pixel_bytes(), scaled.get_rowstride()
        )
        if not texture.save_to_png(str(temporary)):
            return None
        temporary.replace(target)
    except (GLib.Error, OSError, ZeroDivisionError, AttributeError):
        return None
    return str(target)


def _read(path: Path, signature: str, covers_dir: Path) -> BookInfo:
    try:
        info = epub.read_info(path)
    except epub.EpubError:
        return BookInfo(title=path.stem, key=epub.book_key(epub.EpubInfo(), path.name), readable=False)
    cover = None
    if info.cover_path:
        name = hashlib.sha1(f"{path}\n{signature}".encode("utf-8")).hexdigest() + ".png"
        try:
            cover = make_thumbnail(epub.read_cover(path, info.cover_path), covers_dir / name)
        except epub.EpubError:
            cover = None
    return BookInfo(
        title=info.title or path.stem,
        authors=info.authors,
        key=epub.book_key(info, path.name),
        language=info.language,
        cover=cover,
    )


class BookInfoStore:
    """request() calls back in the main thread, right away if the book is known already."""

    def __init__(self):
        directory = config.cache_dir()
        self.file = directory / "books.json"
        self.covers_dir = directory / "covers"
        self.known: dict[str, dict] = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="book-info")
        self._save_timer = 0
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION and isinstance(data.get("books"), dict):
                self.known = data["books"]
        except (OSError, ValueError, AttributeError):
            pass

    def request(self, path: Path, callback) -> None:
        signature = _signature(path)
        if signature is None:
            return
        entry = self.known.get(str(path))
        if isinstance(entry, dict) and entry.get("signature") == signature:
            try:
                info = BookInfo(**entry["info"])
            except (KeyError, TypeError):
                info = None
            if info and (info.cover is None or Path(info.cover).exists()):
                callback(info)
                return
        self._pool.submit(self._work, path, signature, callback)

    def _work(self, path, signature, callback):
        info = _read(path, signature, self.covers_dir)
        GLib.idle_add(self._done, path, signature, info, callback)

    def _done(self, path, signature, info, callback):
        self.known[str(path)] = {"signature": signature, "info": asdict(info)}
        if not self._save_timer:
            self._save_timer = GLib.timeout_add(SAVE_DELAY_MS, self._save)
        callback(info)
        return GLib.SOURCE_REMOVE

    def _save(self):
        self._save_timer = 0
        try:
            atomic_write_text(self.file, json.dumps({"version": CACHE_VERSION, "books": self.known}, ensure_ascii=False))
        except OSError:
            pass
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save()
        self._pool.shutdown(wait=False, cancel_futures=True)
