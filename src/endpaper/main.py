import gettext
import locale
import os
from gettext import gettext as _
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk

from .window import EndpaperWindow

APP_ID = "studio.kleinod.Endpaper"
VERSION = "0.1.0"  # must match meson.build
GETTEXT_DOMAIN = "endpaper"
WEBSITE = "https://kleinod.studio/endpaper"
REPOSITORY = "https://github.com/kleinod-studio/Endpaper"


def _setup_translations(localedir: str | None = None):
    """When installed, the launcher gives the folder; otherwise ./locale in the source folder (run-dev.sh)."""
    if localedir is None:
        source_locale = Path(__file__).resolve().parents[2] / "locale"
        localedir = str(source_locale) if source_locale.is_dir() else None
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        _fall_back_to_utf8()
    for module in (gettext, locale):
        module.bindtextdomain(GETTEXT_DOMAIN, localedir)
    gettext.textdomain(GETTEXT_DOMAIN)


def _fall_back_to_utf8():
    """Without locale data (Flatpak, language pack not installed), don't run in plain ASCII "C",
    or umlauts fail. The translations still follow the requested language."""
    wanted = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
    if wanted and not os.environ.get("LANGUAGE"):
        os.environ["LANGUAGE"] = wanted.split(".")[0]
    os.environ["LC_ALL"] = "C.UTF-8"
    try:
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
    except locale.Error:
        pass


class EndpaperApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Installed, the icon is in the system's icon folders; started from the source folder, it is here.
        source_icons = Path(__file__).resolve().parents[2] / "data"
        if (source_icons / f"{APP_ID}.svg").is_file():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(source_icons))
        for name, callback in (("quit", lambda *_a: self._quit()), ("about", self._show_about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.close", ["<Primary>w"])
        self.set_accels_for_action("win.show-shortcuts", ["<Primary>question"])
        self.set_accels_for_action("win.new-category", ["<Primary>n"])
        self.set_accels_for_action("win.add-books", ["<Primary>o"])
        self.set_accels_for_action("win.search", ["<Primary>f"])
        # F9 is the GNOME default; on Omarchy the system uses F9, hence Ctrl+Shift+L as well.
        self.set_accels_for_action("win.toggle-toc", ["F9", "<Primary><Shift>l"])
        self.set_accels_for_action("win.toggle-marking", ["<Primary>m"])
        self.set_accels_for_action("win.toggle-bookmark", ["<Primary>d"])
        self.set_accels_for_action("win.toggle-notes", ["<Primary><Shift>n"])
        self.set_accels_for_action("win.zoom-in", ["<Primary>plus", "<Primary>equal", "<Primary>KP_Add"])
        self.set_accels_for_action("win.zoom-out", ["<Primary>minus", "<Primary>KP_Subtract"])
        self.set_accels_for_action("win.zoom-reset", ["<Primary>0", "<Primary>KP_0"])

    def do_activate(self):
        window = self.get_active_window() or EndpaperWindow(self)
        window.present()

    def _quit(self):
        if window := self.get_active_window():
            window.close()
        self.quit()

    def _show_about(self, *_a):
        about = Adw.AboutDialog(
            application_name="Endpaper",
            application_icon=APP_ID,
            developer_name="kleinod.studio",
            version=VERSION,
            website=WEBSITE,
            issue_url=f"{REPOSITORY}/issues",
            copyright="© 2026 Sascha Wübbena",
            license_type=Gtk.License.GPL_3_0,
            # Translators: put your name here, it is shown in the About dialog
            translator_credits=_("translator-credits"),
        )
        about.add_legal_section(
            "foliate-js", "© 2022 John Factotum", Gtk.License.MIT_X11, None,
        )
        about.add_legal_section(
            "zip.js", "© Gildas Lormeau", Gtk.License.BSD_3, None,
        )
        about.present(self.get_active_window())


def main(localedir: str | None = None) -> int:
    _setup_translations(localedir)
    return EndpaperApplication().run(None)
