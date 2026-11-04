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
from .notes import NotesError, NotesFile

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
MARK_COLORS = {"yellow": "#f5c211", "green": "#33d17a", "blue": "#62a0ea", "pink": "#f08cc0"}
DEFAULT_MARK_COLOR = "yellow"


def _mark_color_name(color: str) -> str:
    return {"yellow": _("Yellow"), "green": _("Green"), "blue": _("Blue"), "pink": _("Pink")}[color]


def _install_css() -> None:
    rules = "".join(f".mark-{name} {{ background: {hex_color}; }}" for name, hex_color in MARK_COLORS.items())
    provider = Gtk.CssProvider()
    provider.load_from_string(
        rules + """
        .mark-dot {
            min-width: 14px;
            min-height: 14px;
            border-radius: 50%;
            box-shadow: 0 0 0 1px alpha(black, 0.15);
        }
        button.mark-choice { min-width: 26px; min-height: 26px; padding: 0; }
        button.mark-choice:checked .mark-dot {
            box-shadow: 0 0 0 2px var(--popover-bg-color), 0 0 0 3.5px currentColor;
        }
        .mark-bar {
            background-color: var(--popover-bg-color);
            color: var(--popover-fg-color);
            border-radius: 12px;
            box-shadow: 0 0 0 1px alpha(currentColor, 0.1), 0 3px 10px alpha(black, 0.2);
        }
        .mark-bar > viewport > box { padding: 3px 6px; }
        .mark-bar button.suggested-action { min-height: 26px; padding: 0 10px; }
        """
    )
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _web_context() -> WebKit.WebContext:
    context = WebKit.WebContext()
    security = context.get_security_manager()
    security.register_uri_scheme_as_secure("endpaper")
    security.register_uri_scheme_as_cors_enabled("endpaper")
    return context


class ReaderPage(Adw.NavigationPage):
    """on_close(): the page wants to go back to the shelf. on_error(message): a book could not be opened."""

    def __init__(self, state: config.State, on_error, show_external_link, show_message=lambda _text: None):
        super().__init__(title=_("Book"), tag="reader")
        self.state = state
        self.on_error = on_error
        self.show_external_link = show_external_link
        self.show_message = show_message
        self.notes: NotesFile | None = None
        self._notes_monitor = None
        self._mark_guard = False
        self._mark_pending = False
        self._mark_color = DEFAULT_MARK_COLOR
        self._open_mark = None
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
        _install_css()
        self.overlay.add_overlay(self._build_mark_bar())
        self.mark_popover = self._build_mark_popover()
        escape = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        escape.connect("key-pressed", self._on_key)
        self.add_controller(escape)

        view = Adw.ToolbarView(content=self.split)
        view.add_top_bar(header)
        self.set_child(view)

    def _color_buttons(self, on_toggled) -> dict[str, Gtk.ToggleButton]:
        buttons, group = {}, None
        for name in MARK_COLORS:
            title = _mark_color_name(name)
            dot = Gtk.Box(css_classes=["mark-dot", f"mark-{name}"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            button = Gtk.ToggleButton(child=dot, tooltip_text=title, css_classes=["flat", "circular", "mark-choice"], group=group)
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            button.connect("toggled", on_toggled, name)
            buttons[name] = button
            group = group or button
        return buttons

    def _build_mark_bar(self) -> Gtk.Widget:
        """The bar below the text, as in Typecase: a color starts marking, Done ends it."""
        box = Gtk.Box(spacing=2)
        self.mark_buttons = self._color_buttons(self._on_mark_color)
        for button in self.mark_buttons.values():
            box.append(button)
        self.underline_button = Gtk.ToggleButton(
            icon_name="format-text-underline-symbolic", tooltip_text=_("Underline"), css_classes=["flat", "mark-choice"], margin_start=4
        )
        self.underline_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Underline")])
        self.underline_button.connect("toggled", self._on_underline)
        box.append(self.underline_button)
        self.mark_hint = Gtk.Label(visible=False, margin_start=10, margin_end=6)
        self.mark_hint.add_css_class("dim-label")
        box.append(self.mark_hint)
        self.mark_done = Gtk.Button(label=_("Done"), tooltip_text=_("Stop Marking (Esc)"), visible=False, css_classes=["suggested-action"])
        self.mark_done.connect("clicked", lambda _b: self.stop_marking())
        box.append(self.mark_done)
        # Scrollable sideways in a narrow window, instead of making the page wider.
        return Gtk.ScrolledWindow(
            css_classes=["mark-bar"], child=box, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=8,
            margin_start=12, margin_end=12, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True, propagate_natural_height=True,
        )

    def _build_mark_popover(self) -> Gtk.Popover:
        """What can be done with a mark that exists: another color, a note, or remove it."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        row = Gtk.Box(spacing=2)
        self.popover_colors = self._color_buttons(self._on_popover_color)
        for button in self.popover_colors.values():
            row.append(button)
        self.popover_underline = Gtk.ToggleButton(
            icon_name="format-text-underline-symbolic", tooltip_text=_("Underline"), css_classes=["flat", "mark-choice"], margin_start=4
        )
        self.popover_underline.update_property([Gtk.AccessibleProperty.LABEL], [_("Underline")])
        self.popover_underline.connect("toggled", self._on_popover_underline)
        row.append(self.popover_underline)
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        self.popover_remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Remove Mark"), css_classes=["flat"])
        self.popover_remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove Mark")])
        self.popover_remove.connect("clicked", self._on_popover_remove)
        row.append(self.popover_remove)
        box.append(row)
        box.append(Gtk.Label(label=_("Note"), xalign=0, css_classes=["caption", "dim-label"]))
        self.popover_note = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        self.popover_note.update_property([Gtk.AccessibleProperty.LABEL], [_("Note")])
        box.append(Gtk.ScrolledWindow(child=self.popover_note, min_content_width=300, min_content_height=100, css_classes=["card"]))
        popover = Gtk.Popover(child=box, position=Gtk.PositionType.TOP)
        popover.set_parent(self.overlay)
        popover.connect("closed", self._on_popover_closed)
        return popover

    # --- Marks ------------------------------------------------------------------------------

    @property
    def marking(self) -> bool:
        return any(button.get_active() for button in self.mark_buttons.values())

    def _style(self) -> str:
        return "underline" if self.underline_button.get_active() else "highlight"

    def _update_marking(self):
        marking = self.marking
        self.mark_done.set_visible(marking)
        self.mark_hint.set_visible(marking)
        if not marking:
            self.mark_hint.set_label(_("Click the first word, then the last. Double-click marks one word."))
        if self.opened:
            hex_color = MARK_COLORS[self._mark_color]
            self._js(f"endpaper.setMarking({json.dumps(marking)}, {json.dumps(hex_color)})")

    def _on_mark_color(self, button, name):
        if self._mark_guard:
            return
        if button.get_active():
            self._mark_color = name
        self._update_marking()
        self.web.grab_focus()

    def _on_underline(self, button):
        if button.get_active() and not self.marking:
            self.mark_buttons[self._mark_color].set_active(True)
        self.web.grab_focus()

    def start_marking(self) -> None:
        self.mark_buttons[self._mark_color].set_active(True)

    def stop_marking(self) -> None:
        for button in self.mark_buttons.values():
            button.set_active(False)
        self.underline_button.set_active(False)
        self._update_marking()
        self.web.grab_focus()

    def toggle_marking(self) -> None:
        if self.marking:
            self.stop_marking()
        else:
            self.start_marking()

    def _on_key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape and self.marking:
            self.on_escape()
            return True
        return False

    def on_escape(self):
        """First forgets a word that was clicked, then ends marking."""
        if self._mark_pending:
            self._js("endpaper.cancelPendingMark()")
        else:
            self.stop_marking()

    def _save_failed(self):
        self.show_message(_("The notes could not be saved. They are kept until the book is closed."))

    def _mark_data(self, entry) -> dict:
        hex_color = MARK_COLORS.get(entry.color, MARK_COLORS[DEFAULT_MARK_COLOR])
        return {"cfi": entry.cfi, "hex": hex_color, "style": entry.style, "hasNote": bool(entry.text.strip())}

    def _send_marks(self):
        if self.notes:
            self._js(f"endpaper.setMarks({json.dumps([self._mark_data(e) for e in self.notes.entries])})")

    def _on_mark_range(self, message):
        if not self.notes:
            return
        existing = next((e for e in self.notes.entries if e.cfi == message["cfi"]), None)
        try:
            if existing:  # the same place once more: another color, not a second mark
                self.notes.update(existing.id, color=self._mark_color, style=self._style())
            else:
                self.notes.add(message["cfi"], message["quote"], "", message.get("chapter"), self._mark_color, self._style())
        except NotesError:
            self._save_failed()
        entry = next((e for e in self.notes.entries if e.cfi == message["cfi"]), None)
        if entry:
            self._js(f"endpaper.putMark({json.dumps(self._mark_data(entry))})")

    def _on_mark_clicked(self, message):
        entry = next((e for e in self.notes.entries if e.cfi == message["cfi"]), None) if self.notes else None
        if entry is None or self.marking:
            return
        self._open_mark = None
        self._mark_guard = True
        self.popover_colors[entry.color if entry.color in MARK_COLORS else DEFAULT_MARK_COLOR].set_active(True)
        self.popover_underline.set_active(entry.style == "underline")
        self.popover_note.get_buffer().set_text(entry.text)
        self._mark_guard = False
        self._open_mark = entry.id
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = message["x"], message["y"], 1, 1
        self.mark_popover.set_pointing_to(rect)
        self.mark_popover.popup()

    def _change_open_mark(self, **changes):
        if not self._open_mark or not self.notes:
            return
        try:
            self.notes.update(self._open_mark, **changes)
        except NotesError:
            self._save_failed()
        except KeyError:
            self._open_mark = None  # removed by another program meanwhile
        self._send_marks()

    def _on_popover_color(self, button, name):
        if not self._mark_guard and button.get_active():
            self._change_open_mark(color=name)

    def _on_popover_underline(self, button):
        if not self._mark_guard:
            self._change_open_mark(style="underline" if button.get_active() else "highlight")

    def _on_popover_remove(self, _button):
        mark, self._open_mark = self._open_mark, None
        entry = next((e for e in self.notes.entries if e.id == mark), None) if self.notes else None
        if entry:
            try:
                self.notes.remove(mark)
            except NotesError:
                self._save_failed()
            if not any(e.cfi == entry.cfi for e in self.notes.entries):
                self._js(f"endpaper.dropMark({json.dumps(entry.cfi)})")
        self.mark_popover.popdown()

    def _on_popover_closed(self, _popover):
        if self._open_mark and self.notes:
            buffer = self.popover_note.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            entry = next((e for e in self.notes.entries if e.id == self._open_mark), None)
            if entry and entry.text != text:
                self._change_open_mark(text=text)
        self._open_mark = None
        self.web.grab_focus()

    def _open_notes(self, path: Path, title: str):
        self.notes = None
        try:
            self.notes = NotesFile(path, title)
        except NotesError:
            self.show_message(_("The notes of this book could not be read. Marking is off, nothing was changed."))
        for button in self.mark_buttons.values():
            button.set_sensitive(self.notes is not None)
        self.underline_button.set_sensitive(self.notes is not None)
        if self.notes:
            if self.notes.damaged:
                self.show_message(_("The notes file of this book is damaged. It stays as it is, new marks go to a file beside it."))
            self._notes_monitor = Gio.File.new_for_path(str(self.notes.path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._notes_monitor.connect("changed", self._on_notes_changed)

    def _on_notes_changed(self, *_args):
        if not self.notes:
            return
        try:
            if self.notes.refresh():
                self._send_marks()
        except NotesError:
            pass  # the next change or look tries again; nothing is written over an unread file

    def _close_notes(self):
        self.mark_popover.popdown()
        if self._notes_monitor:
            self._notes_monitor.cancel()
            self._notes_monitor = None
        self._flush_notes()
        self.notes = None

    def _flush_notes(self):
        if self.notes:
            try:
                self.notes.flush()
            except NotesError:
                self._save_failed()

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
            self._send_marks()
            self._update_marking()
            if self.on_opened:
                self.on_opened()
        elif kind == "relocate":
            self._on_relocate(message)
        elif kind == "mark-pending":
            self._mark_pending = message["active"]
            self.mark_hint.set_label(
                _("Now click the last word. A second click on this word marks only it.") if message["active"]
                else _("Click the first word, then the last. Double-click marks one word.")
            )
        elif kind == "mark-other-chapter":
            self.show_message(_("A mark cannot go across chapters. This word is the new start."))
        elif kind == "mark-range":
            self._on_mark_range(message)
        elif kind == "mark-clicked":
            self._on_mark_clicked(message)
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
        self._open_notes(path, title)
        self._apply_settings(save=False)
        self.web.load_uri(INDEX_URL)

    def close_book(self) -> None:
        """Saves the position and lets go of the book, so that its memory is free again."""
        self.flush()
        self.stop_marking()
        self._close_notes()
        self.book_path = self.book_key = None
        self.opened = False
        self._clear_toc()
        self.web.load_uri("about:blank")

    def flush(self) -> None:
        self._flush_notes()
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
