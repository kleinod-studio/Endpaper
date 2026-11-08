"""Settings and per-machine state. Nothing here needs to survive: losing it only costs preferences."""

import json
from pathlib import Path

from gi.repository import GLib

from .fileio import atomic_write_text

SORT_MODES = ("title", "author", "recent")
THEMES = ("auto", "light", "sepia", "sage", "dark", "black")
FONTS = ("book", "serif", "sans-serif")
SPACINGS = ("compact", "normal", "relaxed")
WIDTHS = ("narrow", "normal", "wide")
FLOWS = ("paginated", "scrolled")
PROGRESS = ("percent", "pages", "print")  # what the corner shows: percent, pages of the e-book, of the print edition
FONT_SIZE_RANGE = (70, 220)

READER_DEFAULTS = {
    "font_size": 100,
    "font": "book",
    "spacing": "normal",
    "width": "normal",
    "hyphenate": True,
    "theme": "auto",
    "flow": "paginated",
    "progress": "percent",
    "notes_only": False,
    "notes_wide": False,
}
_CHOICES = {"font": FONTS, "spacing": SPACINGS, "width": WIDTHS, "theme": THEMES, "flow": FLOWS, "progress": PROGRESS}


def config_path() -> Path:
    return Path(GLib.get_user_config_dir()) / "endpaper" / "config.json"


def state_path() -> Path:
    return Path(GLib.get_user_data_dir()) / "endpaper" / "state.json"


def cache_dir() -> Path:
    return Path(GLib.get_user_cache_dir()) / "endpaper"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict:
    return _load(config_path())


def update(**values) -> None:
    data = load()
    data.update(values)
    atomic_write_text(config_path(), json.dumps(data, indent=2, ensure_ascii=False))


def library_path() -> Path | None:
    value = load().get("library_path")
    if not isinstance(value, str):
        return None
    path = Path(value)
    return path if path.is_dir() else None


def reader_settings() -> dict:
    """Validated: anything invalid or missing is replaced by the default."""
    stored = load().get("reader")
    stored = stored if isinstance(stored, dict) else {}
    result = dict(READER_DEFAULTS)
    for key, default in READER_DEFAULTS.items():
        value = stored.get(key, default)
        if key in _CHOICES:
            result[key] = value if value in _CHOICES[key] else default
        elif key == "font_size":
            valid = isinstance(value, int) and not isinstance(value, bool)
            result[key] = max(FONT_SIZE_RANGE[0], min(FONT_SIZE_RANGE[1], value)) if valid else default
        else:
            result[key] = value if isinstance(value, bool) else default
    return result


def sort_mode() -> str:
    value = load().get("sort")
    return value if value in SORT_MODES else "title"


class State:
    """Reading positions and folded categories on this computer. Saved after a short delay by the caller."""

    def __init__(self):
        data = _load(state_path())
        books = data.get("books")
        self.books: dict = books if isinstance(books, dict) else {}
        collapsed = data.get("collapsed")
        self.collapsed: set[str] = {c for c in collapsed if isinstance(c, str)} if isinstance(collapsed, list) else set()

    def save(self) -> None:
        atomic_write_text(
            state_path(),
            json.dumps({"books": self.books, "collapsed": sorted(self.collapsed)}, ensure_ascii=False),
        )

    def position(self, key: str) -> str | None:
        entry = self.books.get(key)
        value = entry.get("cfi") if isinstance(entry, dict) else None
        return value if isinstance(value, str) else None

    def last_read(self, key: str) -> float:
        entry = self.books.get(key)
        value = entry.get("read") if isinstance(entry, dict) else 0
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    def fraction(self, key: str) -> float | None:
        entry = self.books.get(key)
        value = entry.get("fraction") if isinstance(entry, dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def set_position(self, key: str, cfi: str, fraction: float, now: float) -> None:
        self.books[key] = {"cfi": cfi, "fraction": fraction, "read": now}
