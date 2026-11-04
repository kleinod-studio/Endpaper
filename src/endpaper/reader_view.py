"""The page for reading a book: a web view showing the book, and the table of contents beside it."""

import json
import posixpath
import time
from gettext import gettext as _
from pathlib import Path

import gi

gi.require_version("WebKit", "6.0")
gi.require_version("Soup", "3.0")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Soup, WebKit

from . import config

WEB_DIR = Path(__file__).parent / "web"
BOOK_PATH = "book"
INDEX_URL = "endpaper://app/index.html"
ALLOWED_NAVIGATION = ("endpaper:", "blob:", "about:")
OPENABLE_LINKS = ("http://", "https://", "mailto:")
SAVE_DELAY_MS = 1500

# Keeps a book's scripts from running and anything from loading from outside. Book content is
# served from blob: URLs, so "script-src 'self'" only ever allows the app's own files.
CSP = (
    "default-src 'self' blob:; script-src 'self'; style-src 'self' blob: 'unsafe-inline'; "
    "img-src 'self' blob: data:; font-src 'self' blob: data:; media-src 'self' blob:; "
    "connect-src 'self' blob:; frame-src blob:; object-src 'none'; form-action 'none'; base-uri 'none'"
)
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript"}
THEME_BACKGROUNDS = {"light": "#ffffff", "sepia": "#f5eedc", "dark": "#222226"}


def _web_context() -> WebKit.WebContext:
    context = WebKit.WebContext()
    security = context.get_security_manager()
    security.register_uri_scheme_as_secure("endpaper")
    security.register_uri_scheme_as_cors_enabled("endpaper")
    return context


class ReaderPage(Adw.NavigationPage):
    """on_close(): the page wants to go back to the shelf. on_error(message): a book could not be opened."""

    def __init__(self, state: config.State, on_error, show_external_link):
        super().__init__(title=_("Book"), tag="reader")
        self.state = state
        self.on_error = on_error
        self.show_external_link = show_external_link
        self.book_path: Path | None = None
        self.book_key: str | None = None
        self.opened = False
        self.settings = config.reader_settings()
        self.toc_rows: dict[str, Gtk.ListBoxRow] = {}
        self._save_timer = 0
        self._last_toc_href = None
        self.on_opened = None  # for tests
        self._build()
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_a: self._apply_settings(save=False))

    def _build(self):
        self.window_title = Adw.WindowTitle()
        header = Adw.HeaderBar(title_widget=self.window_title)

        self.toc_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text=_("Table of Contents"))
        header.pack_start(self.toc_button)
        self.back_button = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Back to Where You Were"), visible=False)
        self.back_button.connect("clicked", lambda _b: self._js("endpaper.back()"))
        header.pack_start(self.back_button)

        settings_button = Gtk.MenuButton(icon_name="font-select-symbolic", tooltip_text=_("Text and Layout"))
        settings_button.set_popover(self._build_settings())
        header.pack_end(settings_button)

        self.toc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.toc_list.add_css_class("navigation-sidebar")
        self.toc_list.connect("row-activated", self._on_toc_row)
        self.toc_list.update_property([Gtk.AccessibleProperty.LABEL], [_("Table of Contents")])
        self.toc_viewport = Gtk.Viewport(child=self.toc_list)
        sidebar = Gtk.ScrolledWindow(child=self.toc_viewport, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.web = self._build_web_view()
        self.overlay = Gtk.Overlay(child=self.web)
        self.prev_button = self._page_button("go-previous-symbolic", _("Previous Page"), Gtk.Align.START, "endpaper.prev()")
        self.next_button = self._page_button("go-next-symbolic", _("Next Page"), Gtk.Align.END, "endpaper.next()")

        self.split = Adw.OverlaySplitView(sidebar=sidebar, content=self.overlay, show_sidebar=False)
        self.toc_button.bind_property(
            "active", self.split, "show-sidebar", GObject.BindingFlags.BIDIRECTIONAL
        )
        view = Adw.ToolbarView(content=self.split)
        view.add_top_bar(header)
        self.set_child(view)

    def _page_button(self, icon, label, align, script):
        button = Gtk.Button(icon_name=icon, tooltip_text=label, halign=align, valign=Gtk.Align.CENTER)
        button.set_margin_start(12)
        button.set_margin_end(12)
        button.add_css_class("circular")
        button.add_css_class("osd")
        button.connect("clicked", lambda _b: self._js(script))
        self.overlay.add_overlay(button)
        return button

    def set_narrow(self, narrow: bool) -> None:
        """In a narrow window the text needs all the room."""
        self.prev_button.set_visible(not narrow)
        self.next_button.set_visible(not narrow)
        self.split.set_collapsed(narrow)

    def _combo(self, title, choices, key):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _value, label in choices]))
        values = [value for value, _label in choices]
        row.set_selected(values.index(self.settings[key]))

        def changed(combo, _param):
            self.settings[key] = values[combo.get_selected()]
            self._apply_settings()
        row.connect("notify::selected", changed)
        return row

    def _build_settings(self) -> Gtk.Popover:
        group = Adw.PreferencesGroup()
        low, high = config.FONT_SIZE_RANGE
        self.size_row = Adw.SpinRow.new_with_range(low, high, 10)
        self.size_row.set_title(_("Text Size (%)"))
        self.size_row.set_value(self.settings["font_size"])
        self.size_row.connect("notify::value", self._on_size_row)
        group.add(self.size_row)
        group.add(self._combo(_("Font"), [("book", _("As in the Book")), ("serif", _("Serif")), ("sans-serif", _("Sans Serif"))], "font"))
        group.add(self._combo(_("Line Spacing"), [("compact", _("Compact")), ("normal", _("Normal")), ("relaxed", _("Relaxed"))], "spacing"))
        group.add(self._combo(_("Text Width"), [("narrow", _("Narrow")), ("normal", _("Normal")), ("wide", _("Wide"))], "width"))
        hyphen = Adw.SwitchRow(title=_("Hyphenation"), active=self.settings["hyphenate"])

        def hyphen_changed(row, _param):
            self.settings["hyphenate"] = row.get_active()
            self._apply_settings()
        hyphen.connect("notify::active", hyphen_changed)
        group.add(hyphen)
        group.add(self._combo(_("Colors"), [("auto", _("Follow System")), ("light", _("Light")), ("sepia", _("Sepia")), ("dark", _("Dark"))], "theme"))
        group.add(self._combo(_("Layout"), [("paginated", _("Pages")), ("scrolled", _("Continuous Scrolling"))], "flow"))
        group.set_size_request(320, -1)
        popover = Gtk.Popover(child=group)
        return popover

    def _on_size_row(self, row, _param):
        self.settings["font_size"] = int(row.get_value())
        self._apply_settings()

    def change_font_size(self, delta: int | None) -> None:
        """delta None resets to the normal size."""
        low, high = config.FONT_SIZE_RANGE
        size = 100 if delta is None else max(low, min(high, self.settings["font_size"] + delta))
        self.size_row.set_value(size)

    def _resolved_settings(self) -> dict:
        resolved = dict(self.settings)
        if resolved["theme"] == "auto":
            resolved["theme"] = "dark" if Adw.StyleManager.get_default().get_dark() else "light"
        return resolved

    def _apply_settings(self, save=True):
        resolved = self._resolved_settings()
        color = Gdk.RGBA()
        color.parse(THEME_BACKGROUNDS[resolved["theme"]])
        self.web.set_background_color(color)
        if self.opened:
            self._js(f"endpaper.applySettings({json.dumps(resolved)})")
        if save:
            config.update(reader=self.settings)

    # --- Web view ------------------------------------------------------------------------

    def _build_web_view(self) -> WebKit.WebView:
        context = _web_context()
        context.register_uri_scheme("endpaper", self._serve)
        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("endpaper", None)
        manager.connect("script-message-received::endpaper", self._on_message)
        # Ephemeral: nothing about the books ends up in a browser cache on disk.
        session = WebKit.NetworkSession.new_ephemeral()
        web = WebKit.WebView(
            web_context=context, user_content_manager=manager, network_session=session, hexpand=True, vexpand=True
        )
        settings = web.get_settings()
        settings.set_enable_javascript(True)  # our own reader code; books are held back by the CSP
        settings.set_javascript_can_open_windows_automatically(False)
        settings.set_javascript_can_access_clipboard(False)
        settings.set_enable_developer_extras(False)
        settings.set_allow_file_access_from_file_urls(False)
        settings.set_allow_universal_access_from_file_urls(False)
        settings.set_enable_webrtc(False)
        settings.set_enable_media(False)
        settings.set_enable_webaudio(False)
        settings.set_enable_webgl(False)
        settings.set_enable_html5_database(False)
        settings.set_enable_html5_local_storage(False)
        settings.set_enable_page_cache(False)
        web.connect("decide-policy", self._on_policy)
        web.connect("permission-request", lambda _w, request: (request.deny(), True)[1])
        web.connect("context-menu", self._on_context_menu)
        web.connect("web-process-terminated", self._on_process_terminated)
        web.update_property([Gtk.AccessibleProperty.LABEL], [_("Book Text")])
        return web

    def _on_context_menu(self, _web, menu, _hit):
        """Only what makes sense while reading: no reload, no opening of links in a new window."""
        keep = {WebKit.ContextMenuAction.COPY}
        for item in list(menu.get_items()):
            if item.get_stock_action() not in keep:
                menu.remove(item)
        return menu.get_n_items() == 0

    def _serve(self, request):
        path = posixpath.normpath(request.get_path() or "/").lstrip("/")
        stream, length, content_type = None, 0, "application/octet-stream"
        try:
            if path == BOOK_PATH:
                if self.book_path:
                    file = Gio.File.new_for_path(str(self.book_path))
                    length = file.query_info("standard::size", Gio.FileQueryInfoFlags.NONE, None).get_size()
                    stream = file.read(None)
                    content_type = "application/epub+zip"
            else:
                target = (WEB_DIR / path).resolve()
                if target.is_file() and target.is_relative_to(WEB_DIR.resolve()):
                    data = GLib.Bytes.new(target.read_bytes())
                    stream, length = Gio.MemoryInputStream.new_from_bytes(data), data.get_size()
                    content_type = CONTENT_TYPES.get(target.suffix, content_type)
        except (GLib.Error, OSError) as error:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), str(error), Gio.IOErrorEnum.FAILED))
            return
        if stream is None:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "Not found", Gio.IOErrorEnum.NOT_FOUND))
            return
        response = WebKit.URISchemeResponse.new(stream, length)
        response.set_content_type(content_type)
        response.set_status(200, "OK")
        headers = Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
        headers.append("Content-Security-Policy", CSP)
        response.set_http_headers(headers)
        request.finish_with_response(response)

    def _on_policy(self, _web, decision, kind):
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            if not decision.is_mime_type_supported():
                decision.ignore()
                return True
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if not uri.startswith(ALLOWED_NAVIGATION):
            decision.ignore()
            return True
        return False

    def _on_process_terminated(self, _web, _reason):
        if self.book_path:
            self.on_error(_("The book could not be shown any further."))

    def _js(self, script: str, callback=None):
        def done(web, result):
            try:
                value = web.evaluate_javascript_finish(result)
            except GLib.Error:
                value = None
            if callback:
                callback(json.loads(value.to_json(0)) if value is not None and not value.is_undefined() else None)
        self.web.evaluate_javascript(script, -1, None, None, None, done)

    def _on_message(self, _manager, value):
        message = json.loads(value.to_string())
        kind = message["type"]
        if kind == "ready":
            if self.book_path:
                arguments = [
                    f"endpaper://app/{BOOK_PATH}",
                    self.book_path.name,
                    self._resolved_settings(),
                    self.state.position(self.book_key),
                ]
                self._js(f"endpaper.open(...{json.dumps(arguments)})")
        elif kind == "opened":
            self.opened = True
            self._fill_toc(message["toc"])
            self._highlight_toc(self._last_toc_href)
            if self.on_opened:
                self.on_opened()
        elif kind == "relocate":
            self._on_relocate(message)
        elif kind == "external-link":
            href = message["href"]
            if href.startswith(OPENABLE_LINKS):
                self.show_external_link(href)
        elif kind == "error":
            self.on_error(_("This file could not be opened as a book."))

    # --- Book ---------------------------------------------------------------------------

    def open_book(self, path: Path, key: str, title: str) -> None:
        self.book_path, self.book_key = path, key
        self.opened = False
        self.set_title(title)
        self.window_title.set_title(title)
        self.window_title.set_subtitle("")
        self.back_button.set_visible(False)
        self._clear_toc()
        self._apply_settings(save=False)
        self.web.load_uri(INDEX_URL)

    def close_book(self) -> None:
        """Saves the position and lets go of the book, so that its memory is free again."""
        self.flush()
        self.book_path = self.book_key = None
        self.opened = False
        self._clear_toc()
        self.web.load_uri("about:blank")

    def flush(self) -> None:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
            self._save_state()

    def _save_state(self) -> bool:
        self._save_timer = 0
        try:
            self.state.save()
        except OSError:
            pass  # only the reading position; the next save tries again
        return GLib.SOURCE_REMOVE

    def _on_relocate(self, message):
        if self.book_key and message.get("cfi"):
            self.state.set_position(self.book_key, message["cfi"], message["fraction"], time.time())
            if not self._save_timer:
                self._save_timer = GLib.timeout_add(SAVE_DELAY_MS, self._save_state)
        percent = round(message["fraction"] * 100)
        progress = _("{percent}%").format(percent=percent)
        chapter = message.get("tocLabel")
        self.window_title.set_subtitle(f"{chapter} · {progress}" if chapter else progress)
        self.back_button.set_visible(bool(message.get("canGoBack")))
        self._highlight_toc(message.get("tocHref"))

    # --- Table of contents ------------------------------------------------------------------

    def _clear_toc(self):
        self.toc_list.remove_all()
        self.toc_rows = {}
        self._last_toc_href = None

    def _fill_toc(self, items):
        for item in items:
            label = Gtk.Label(
                label=item["label"], xalign=0, ellipsize=3, margin_start=item["depth"] * 14, tooltip_text=item["label"]
            )
            row = Gtk.ListBoxRow(child=label)
            row.href = item["href"]
            self.toc_list.append(row)
            self.toc_rows.setdefault(item["href"], row)

    def _highlight_toc(self, href):
        self._last_toc_href = href
        row = self.toc_rows.get(href)
        if row and self.toc_list.get_selected_row() is not row:
            self.toc_list.select_row(row)
            self.toc_viewport.scroll_to(row, None)

    def _on_toc_row(self, _list, row):
        self._js(f"endpaper.goToHref({json.dumps(row.href)})")
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.web.grab_focus()

    def toggle_toc(self):
        self.toc_button.set_active(not self.toc_button.get_active())

    def focus_text(self):
        self.web.grab_focus()
