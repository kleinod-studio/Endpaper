"""The colors of the current Omarchy theme, for the theme "Omarchy": the pages of the book and the
whole window take them, and follow when another theme is set in Omarchy. As in Typecase."""

import re
import tomllib
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

FOLDER = Path.home() / ".local" / "state" / "omarchy" / "current"
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

_colors: dict | None = None
_loaded = False
_monitor: Gio.FileMonitor | None = None
_timeout = 0
_callbacks: list = []
_provider: Gtk.CssProvider | None = None
_window_on = False


def mix(a: str, b: str, amount: float) -> str:
    """Hex color a moved towards b by amount (0..1)."""
    channels = [round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * amount) for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _rgb(color: str, alpha: float | None = None) -> str:
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})" if alpha is not None else f"rgb({red}, {green}, {blue})"


def _luminance(color: str) -> float:
    red, green, blue = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def parse(values: dict) -> dict | None:
    """The colors Endpaper needs from an Omarchy theme's colors.toml; None without text and background."""
    def color(key, fallback=None):
        value = values.get(key)
        return value.lower() if isinstance(value, str) and _HEX.fullmatch(value) else fallback

    text, background = color("foreground"), color("background")
    if not text or not background:
        return None
    dark = values.get("mode") == "dark" if values.get("mode") in ("dark", "light") else _luminance(background) < 0.5
    return {
        "dark": dark,
        "background": background,
        "text": text,
        "accent": color("accent", color("blue", text)),
        "surface": color("lighter_background", mix(background, text, 0.08)),
    }


def read(folder: Path | None = None) -> dict | None:
    try:
        with open((folder or FOLDER) / "theme" / "colors.toml", "rb") as file:
            return parse(tomllib.load(file))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None


def colors() -> dict | None:
    """The current Omarchy colors, None where Omarchy isn't there."""
    global _colors, _loaded
    if not _loaded:
        _colors, _loaded = read(), True
    return _colors


def page_theme(found: dict) -> dict:
    """The theme for the pages of the book, with what reader.js needs for one."""
    background, text = found["background"], found["text"]
    return {
        "bg": background, "fg": text, "scheme": "dark" if found["dark"] else "light", "adapt": True, "dark": found["dark"],
        "peek": _rgb(mix(background, text, 0.1 if found["dark"] else 0.03), 0.86),
        "bgRgb": _rgb(background), "fgRgb": _rgb(text), "surfaceRgb": _rgb(found["surface"]),
    }


def window_css(found: dict) -> str:
    """libadwaita colors for the whole window, as in Typecase: header bars blend with the page, sidebars
    and cards are shades of the same background, and the accent is the theme's accent."""
    background, text, accent = found["background"], found["text"], found["accent"]
    on_accent = "#000000" if _luminance(accent) > 0.6 else "#ffffff"
    sidebar = mix(background, text, 0.06)
    raised = mix(background, text, 0.03)
    card = mix(background, text, 0.10) if found["dark"] else background
    return (
        ":root {"
        f" --window-bg-color: {raised}; --window-fg-color: {text};"
        f" --view-bg-color: {background}; --view-fg-color: {text};"
        f" --headerbar-bg-color: {background}; --headerbar-fg-color: {text}; --headerbar-backdrop-color: {background};"
        f" --sidebar-bg-color: {sidebar}; --sidebar-fg-color: {text}; --sidebar-backdrop-color: {sidebar};"
        f" --secondary-sidebar-bg-color: {sidebar}; --secondary-sidebar-fg-color: {text}; --secondary-sidebar-backdrop-color: {sidebar};"
        f" --popover-bg-color: {raised}; --popover-fg-color: {text};"
        f" --dialog-bg-color: {raised}; --dialog-fg-color: {text};"
        f" --card-bg-color: {card}; --card-fg-color: {text};"
        f" --accent-bg-color: {accent}; --accent-fg-color: {on_accent}; --accent-color: {accent};"
        " }"
    )


def set_window(on: bool) -> None:
    """The whole window in the Omarchy colors, or back to the system's."""
    global _provider, _window_on
    found = colors() if on else None
    display = Gdk.Display.get_default()
    if _provider is not None and display is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _provider)
        _provider = None
    manager = Adw.StyleManager.get_default()
    if found is None:
        if _window_on:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        _window_on = False
        return
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if found["dark"] else Adw.ColorScheme.FORCE_LIGHT)
    if display is not None:
        _provider = Gtk.CssProvider()
        _provider.load_from_string(window_css(found))
        Gtk.StyleContext.add_provider_for_display(display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    _window_on = True


def reload() -> bool:
    """Reads the colors again; whether they changed."""
    global _colors, _loaded
    found = read()
    changed = found != _colors
    _colors, _loaded = found, True
    if changed and _window_on:
        set_window(True)
    return changed


def watch(on_change=None) -> None:
    """Follows the theme set in Omarchy, and calls on_change() after another one was set."""
    global _monitor
    if on_change:
        _callbacks.append(on_change)
    if _monitor is not None or not FOLDER.is_dir():
        return

    def changed():
        global _timeout
        _timeout = 0
        if reload():
            for callback in list(_callbacks):
                callback()
        return GLib.SOURCE_REMOVE

    def on_event(*_args):
        # Omarchy replaces several files in a row; wait until it is done.
        global _timeout
        if _timeout:
            GLib.source_remove(_timeout)
        _timeout = GLib.timeout_add(400, changed)

    try:
        _monitor = Gio.File.new_for_path(str(FOLDER)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
    except GLib.Error:
        return
    _monitor.connect("changed", on_event)
