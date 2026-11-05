from gettext import gettext as _

from gi.repository import Adw, Gio, Gtk

from . import config
from .shelf import ShelfPage


class EndpaperWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        data = config.load()
        width, height = data.get("window_width"), data.get("window_height")
        valid = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 360
        super().__init__(
            application=application, title="Endpaper",
            default_width=width if valid(width) else 1100, default_height=height if valid(height) else 760,
        )
        self.set_size_request(360, 294)
        self.state = config.State()
        self.reader = None  # created on first use: the web engine takes a moment to load

        self.toasts = Adw.ToastOverlay()
        self.navigation = Adw.NavigationView()
        self.toasts.set_child(self.navigation)
        self.set_content(self.toasts)

        self.shelf = ShelfPage(self, self.state, self.open_book, self.show_toast)
        self.navigation.add(self.shelf)
        self.navigation.connect("popped", self._on_popped)
        self.navigation.connect("notify::visible-page", lambda *_a: self._update_actions())

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820sp"))
        narrow.connect("apply", lambda _b: self._set_narrow(True))
        narrow.connect("unapply", lambda _b: self._set_narrow(False))
        self.add_breakpoint(narrow)
        self.narrow = False

        self._install_actions()
        self._update_actions()
        self.connect("close-request", self._on_close)
        self.shelf.set_root(config.library_path())

    def _install_actions(self):
        self.reader_actions = []
        for name, callback, reader_only in (
            ("close", lambda *_a: self.close(), False),
            ("show-shortcuts", self._show_shortcuts, False),
            ("new-category", lambda *_a: self.shelf.actions.activate_action("new-category", None), False),
            ("add-books", lambda *_a: self.shelf.actions.activate_action("add-books", None), False),
            ("search", lambda *_a: self.reader.start_search() if self.navigation.get_visible_page() is not self.shelf else self.shelf.start_search(), False),
            ("toggle-toc", lambda *_a: self.reader.toggle_toc(), True),
            ("toggle-notes", lambda *_a: self.reader.toggle_notes(), True),
            ("toggle-marking", lambda *_a: self.reader.toggle_marking(), True),
            ("toggle-bookmark", lambda *_a: self.reader.toggle_bookmark(), True),
            ("zoom-in", lambda *_a: self.reader.change_font_size(10), True),
            ("zoom-out", lambda *_a: self.reader.change_font_size(-10), True),
            ("zoom-reset", lambda *_a: self.reader.change_font_size(None), True),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if reader_only:
                self.reader_actions.append(action)
        self.shelf_actions = [self.lookup_action(n) for n in ("new-category", "add-books")]

    def _update_actions(self):
        reading = self.navigation.get_visible_page() is not self.shelf
        for action in self.reader_actions:
            action.set_enabled(reading)
        for action in self.shelf_actions:
            action.set_enabled(not reading)

    def _set_narrow(self, narrow: bool):
        self.narrow = narrow
        if self.reader:
            self.reader.set_narrow(narrow)

    def open_book(self, book, info):
        if self.reader is None:
            from .reader_view import ReaderPage  # the web engine loads only when it is needed

            self.reader = ReaderPage(self.state, self._on_reader_error, self._ask_external_link, self.show_toast)
            self.reader.set_narrow(self.narrow)
        # The page comes first: laying out a book needs the size of the finished view.
        self.navigation.push(self.reader)
        self.reader.open_book(book.path, info.key, info.title)
        self.reader.focus_text()

    def _on_popped(self, _view, page):
        if page is self.reader:
            self.reader.close_book()
            self.shelf.refresh_progress()

    def _on_reader_error(self, message: str):
        self.show_toast(message)
        if self.navigation.get_visible_page() is self.reader:
            self.navigation.pop()

    def _ask_external_link(self, uri: str):
        """Reading stays offline: a link only ever leaves through the browser, and only after asking."""
        dialog = Adw.AlertDialog(heading=_("Open Link in Browser?"), body=uri)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response == "open":
                Gtk.UriLauncher(uri=uri).launch(self, None, None)
        dialog.connect("response", respond)
        dialog.present(self)

    def show_toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _on_close(self, _window):
        if self.reader:
            self.reader.flush()
        self.shelf.info_store.shutdown()
        if not self.is_maximized() and not self.is_fullscreen():
            width, height = self.get_default_size()
            try:
                config.update(window_width=width, window_height=height)
            except OSError:
                pass
        return False

    def _show_shortcuts(self, *_a):
        dialog = Adw.ShortcutsDialog()
        shelf = Adw.ShortcutsSection(title=_("Bookshelf"))
        for title, accel in (
            (_("New Category"), "<Primary>n"),
            (_("Add Books"), "<Primary>o"),
            (_("Find a Book"), "<Primary>f"),
            (_("Pin or Unpin the Book"), "<Primary>p"),
            (_("Rename the Book"), "F2"),
            (_("Move the Book to the Trash"), "Delete"),
            (_("Move the Book to the Previous Category"), "<Alt>Up"),
            (_("Move the Book to the Next Category"), "<Alt>Down"),
            (_("Book Menu"), "Menu"),
        ):
            shelf.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(shelf)
        reading = Adw.ShortcutsSection(title=_("Reading"))
        for title, accel in (
            (_("Next Page"), "Right Page_Down space"),
            (_("Previous Page"), "Left Page_Up <Shift>space"),
            (_("Back to Where You Were"), "BackSpace"),
            (_("Table of Contents"), "F9"),
            (_("Search in the Book"), "<Primary>f"),
            (_("Next or Previous Result"), "Return <Shift>Return"),
            (_("Notes"), "<Primary><Shift>n"),
            (_("Set or Remove a Bookmark"), "<Primary>d"),
            (_("Larger Text"), "<Primary>plus"),
            (_("Smaller Text"), "<Primary>minus"),
            (_("Normal Text Size"), "<Primary>0"),
            (_("Back to the Shelf"), "<Alt>Left"),
        ):
            reading.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(reading)
        marking = Adw.ShortcutsSection(title=_("Marking"))
        for title, accel in (
            (_("Start or Stop Marking"), "<Primary>m"),
            (_("Go to Another Word"), "Left Right Up Down"),
            (_("Choose the First or Last Word"), "Return space"),
            (_("Forget the Chosen Word, Then Stop Marking"), "Escape"),
            (_("Choose a Color"), "1 2 3 4 5"),
            (_("Underline"), "u"),
            (_("Options of the Mark at This Word"), "Menu <Shift>F10"),
            (_("Undo the Last Change to a Mark"), "<Primary>z"),
            (_("Restore the Change"), "<Primary><Shift>z"),
        ):
            marking.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(marking)
        general = Adw.ShortcutsSection(title=_("General"))
        for title, accel in ((_("Keyboard Shortcuts"), "<Primary>question"), (_("Close Window"), "<Primary>w"), (_("Quit"), "<Primary>q")):
            general.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(general)
        dialog.present(self)
