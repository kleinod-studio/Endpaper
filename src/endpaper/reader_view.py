"""The page for reading a book: a web view showing the book, and the table of contents beside it."""

import json
import posixpath
import time
from gettext import gettext as _, ngettext
from pathlib import Path

import gi

gi.require_version("WebKit", "6.0")
gi.require_version("Soup", "3.0")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Soup, WebKit

from . import config
from .notes import DEFAULT_MARK_COLOR, MARK_COLORS, NotesError, NotesFile, NotesKept
from .notes_panel import NotesPanel

WEB_DIR = Path(__file__).parent / "web"
BOOK_PATH = "book"
INDEX_URL = "endpaper://app/index.html"
ALLOWED_NAVIGATION = ("endpaper:", "blob:", "about:")
OPENABLE_LINKS = ("http://", "https://", "mailto:")
SAVE_DELAY_MS = 1500
# The notes flyout, normal and doubled: never narrower than the first number, never wider than the last.
NOTES_MIN_WIDTH = 300
NOTES_WIDTH = (0.3, 420)
NOTES_WIDE_WIDTH = (0.6, 840)

# Keeps a book's scripts from running and anything from loading from outside. Book content is
# served from blob: URLs, so "script-src 'self'" only ever allows the app's own files.
CSP = (
    "default-src 'self' blob:; script-src 'self'; style-src 'self' blob: 'unsafe-inline'; "
    "img-src 'self' blob: data:; font-src 'self' blob: data:; media-src 'self' blob:; "
    "connect-src 'self' blob:; frame-src blob:; object-src 'none'; form-action 'none'; base-uri 'none'"
)
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript"}
# Background and text of each theme. reader.js has the same values and what else a book needs.
THEME_COLORS = {
    "light": ("#ffffff", "#2e2e32"),
    "sepia": ("#f5eedc", "#40342a"),
    "sage": ("#e4ecdf", "#2c3a2e"),
    "dark": ("#222226", "#e3e3e6"),
    "black": ("#000000", "#d6d6d6"),
}
MARK_KEYS = {
    Gdk.KEY_Left: "left", Gdk.KEY_Right: "right", Gdk.KEY_Up: "up", Gdk.KEY_Down: "down",
    Gdk.KEY_Return: "choose", Gdk.KEY_KP_Enter: "choose", Gdk.KEY_space: "choose",
    Gdk.KEY_Page_Down: "page-next", Gdk.KEY_Page_Up: "page-previous", Gdk.KEY_Menu: "menu",
}


def _mark_color_name(color: str) -> str:
    return {"yellow": _("Yellow"), "green": _("Green"), "blue": _("Blue"), "pink": _("Pink"), "purple": _("Purple")}[color]


def _theme_name(theme: str) -> str:
    return {"light": _("Light"), "sepia": _("Sepia"), "sage": _("Sage"), "dark": _("Dark"), "black": _("Black")}[theme]


def _install_css() -> None:
    rules = "".join(
        f".mark-{name} {{ background: {hex_color}; }} .note-tint-{name} {{ background-color: alpha({hex_color}, 0.32); }}"
        for name, hex_color in MARK_COLORS.items()
    )
    rules += "".join(
        f"button.theme-{name}, button.theme-{name}:hover, button.theme-{name}:checked {{ background: {back}; color: {text}; }}"
        for name, (back, text) in THEME_COLORS.items()
    )
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
        menubutton.mark-choice > button { min-width: 26px; min-height: 26px; padding: 0; }
        button.theme-choice {
            min-width: 40px;
            min-height: 40px;
            padding: 0;
            border-radius: 50%;
            box-shadow: inset 0 0 0 1px alpha(gray, 0.5);
        }
        button.theme-choice:checked { box-shadow: 0 0 0 2px var(--popover-bg-color), 0 0 0 4px var(--accent-bg-color); }
        .note-card { padding: 6px; }
        .note-card > button { font-weight: normal; padding: 6px; }
        .note-loose { padding: 10px; }
        .note-view { background: none; font-size: 0.95em; }
        .note-row { padding: 6px; border-top: 1px solid alpha(currentColor, 0.25); }
        .note-row:focus-within { background-color: alpha(var(--view-bg-color), 0.5); }
        .note-row button { opacity: 0.6; }
        .note-row button:hover, .note-row button:focus-visible { opacity: 1; }
        .note-quote { font-style: italic; }
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
        self.remove_dialog = None
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

        menu = Gio.Menu()
        menu.append(_("Keyboard Shortcuts"), "win.show-shortcuts")
        menu.append(_("About Endpaper"), "app.about")
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text=_("Main Menu"), menu_model=menu, primary=True)
        header.pack_end(self.menu_button)
        self.notes_button = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", tooltip_text=_("Notes"))
        self.notes_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Notes")])
        header.pack_end(self.notes_button)

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

        content = Adw.ToolbarView(content=self.overlay)
        content.add_top_bar(header)
        self.split = Adw.OverlaySplitView(sidebar=self._sidebar_page(_("Table of Contents"), sidebar), content=content, show_sidebar=False)
        self.toc_button.bind_property(
            "active", self.split, "show-sidebar", GObject.BindingFlags.BIDIRECTIONAL
        )
        _install_css()
        self.overlay.add_overlay(self._build_mark_bar())
        self.mark_popover = self._build_mark_popover()
        escape = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        escape.connect("key-pressed", self._on_key)
        self.add_controller(escape)

        self.notes_panel = NotesPanel(
            self._go_to_mark, self._put_mark, self._save_failed, self.settings["notes_only"], self._on_notes_only,
        )
        self.notes_wide_button = Gtk.ToggleButton(
            icon_name="view-fullscreen-symbolic", tooltip_text=_("Double Width"), active=self.settings["notes_wide"],
        )
        self.notes_wide_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Double Width")])
        self.notes_wide_button.connect("toggled", self._on_notes_wide)
        self.notes_split = Adw.OverlaySplitView(
            content=self.split, sidebar=self._sidebar_page(_("Notes"), self.notes_panel, self.notes_wide_button),
            sidebar_position=Gtk.PackType.END, show_sidebar=False, min_sidebar_width=NOTES_MIN_WIDTH,
        )
        self._set_notes_width()
        self.notes_button.bind_property("active", self.notes_split, "show-sidebar", GObject.BindingFlags.BIDIRECTIONAL)
        self.notes_split.connect("notify::show-sidebar", self._on_notes_shown)

        self.set_child(self.notes_split)

    def _set_notes_width(self):
        fraction, widest = NOTES_WIDE_WIDTH if self.settings["notes_wide"] else NOTES_WIDTH
        self.notes_split.set_max_sidebar_width(widest)
        self.notes_split.set_sidebar_width_fraction(fraction)

    def _on_notes_wide(self, button):
        self.settings["notes_wide"] = button.get_active()
        self._set_notes_width()
        config.update(reader=self.settings)

    def _sidebar_page(self, title: str, child: Gtk.Widget, start: Gtk.Widget | None = None) -> Gtk.Widget:
        """A sidebar reaches up to the window's edge and has a title bar of its own, as in Typecase."""
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title), show_back_button=False)
        if start:
            header.pack_start(start)
        page = Adw.ToolbarView(content=child)
        page.add_top_bar(header)
        return page

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
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4, margin_top=4, margin_bottom=4))
        self.settings_popover = self._build_settings()
        self.settings_button = Gtk.MenuButton(
            icon_name="font-select-symbolic", tooltip_text=_("Text and Layout"), has_frame=False, direction=Gtk.ArrowType.UP,
            popover=self.settings_popover, css_classes=["mark-choice"],
        )
        box.append(self.settings_button)
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
        """What can be done with a mark that exists: another color, remove it, or add a note."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
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
        self.popover_remove = Gtk.Button(
            icon_name="user-trash-symbolic", tooltip_text=_("Remove Mark"), css_classes=["flat", "mark-choice"], margin_start=4
        )
        self.popover_remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove Mark")])
        self.popover_remove.connect("clicked", self._on_popover_remove)
        row.append(self.popover_remove)
        box.append(row)
        self.popover_add_note = Gtk.Button(label=_("Add Note"), css_classes=["flat"])
        self.popover_add_note.connect("clicked", self._on_popover_add_note)
        box.append(self.popover_add_note)
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

    def _on_key(self, _controller, keyval, _code, state):
        if self.mark_popover.get_visible() or self.settings_popover.get_visible():
            return False  # a menu has its own keys
        focus = self.get_root().get_focus() if self.get_root() else None
        in_notes = focus is not None and focus.is_ancestor(self.notes_panel)
        if keyval in (Gdk.KEY_z, Gdk.KEY_Z) and state & Gdk.ModifierType.CONTROL_MASK:
            if isinstance(focus, (Gtk.TextView, Gtk.Text)):
                return False  # a text field has its own undo
            self._undo_mark(redo=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if keyval == Gdk.KEY_Escape:
            if self.marking and not in_notes:
                self.on_escape()
                return True
            if in_notes:
                self.notes_panel.flush()
                self.notes_button.set_active(False)
                return True
            return False
        if not self.marking or (focus is not None and focus is not self.web):
            return False
        if keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK:
            self._js('endpaper.markKey("menu")')
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        name = MARK_KEYS.get(keyval)
        if name:
            self._js(f"endpaper.markKey({json.dumps(name)})")
            return True
        if keyval == Gdk.KEY_u:
            self.underline_button.set_active(not self.underline_button.get_active())
            return True
        number = keyval - Gdk.KEY_1
        if 0 <= number < len(MARK_COLORS):
            self.mark_buttons[list(MARK_COLORS)[number]].set_active(True)
            return True
        return False

    def _undo_mark(self, redo: bool = False):
        if not self.notes:
            return
        self.notes_panel.flush()
        try:
            done = self.notes.redo() if redo else self.notes.undo()
        except NotesKept:
            self.show_message(_("Left as it is, so that nothing you wrote gets lost"))
            return
        except NotesError:
            self._save_failed()
            done = True
        if done:
            self._send_marks()
            self._notes_changed()
            self.show_message(_("Change restored") if redo else _("Change undone"))
        else:
            self.show_message(_("There is nothing to restore") if redo else _("There is nothing to undo"))

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
        return {"cfi": entry.cfi, "hex": hex_color, "style": entry.style, "hasNote": entry.has_note}

    def _send_marks(self):
        if self.notes:
            self._js(f"endpaper.setMarks({json.dumps([self._mark_data(e) for e in self.notes.entries])})")

    def _put_mark(self, entry):
        self._js(f"endpaper.putMark({json.dumps(self._mark_data(entry))})")

    def _go_to_mark(self, cfi):
        self._js(f"endpaper.goToHref({json.dumps(cfi)})")
        if self.notes_split.get_collapsed():
            self.notes_split.set_show_sidebar(False)

    def toggle_notes(self):
        self.notes_button.set_active(not self.notes_button.get_active())
        if self.notes_button.get_active():
            self.notes_panel.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def _on_notes_shown(self, *_args):
        if self.notes_split.get_show_sidebar():
            if self.notes_panel.dirty:
                self.notes_panel.rebuild()
        else:
            self.notes_panel.flush()
            self.web.grab_focus()

    def _notes_changed(self):
        """Building the list takes a moment: it waits until the flyout is open."""
        if self.notes_split.get_show_sidebar():
            self.notes_panel.rebuild()
        else:
            self.notes_panel.dirty = True

    def show_mark_notes(self, entry_id: str, new_note: bool = False):
        self.notes_button.set_active(True)
        self.notes_panel.show_entry(entry_id, new_note)

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
            self._put_mark(entry)
        self._notes_changed()

    def _on_mark_clicked(self, message):
        entry = next((e for e in self.notes.entries if e.cfi == message["cfi"]), None) if self.notes else None
        if entry is None or (self.marking and not message.get("menu")):
            return
        if message.get("sign"):
            self.show_mark_notes(entry.id)
            return
        self._open_mark = None
        self._mark_guard = True
        self.popover_colors[entry.color if entry.color in MARK_COLORS else DEFAULT_MARK_COLOR].set_active(True)
        self.popover_underline.set_active(entry.style == "underline")
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
        self._notes_changed()

    def _on_popover_color(self, button, name):
        if not self._mark_guard and button.get_active():
            self._change_open_mark(color=name)

    def _on_popover_underline(self, button):
        if not self._mark_guard:
            self._change_open_mark(style="underline" if button.get_active() else "highlight")

    def _on_popover_remove(self, _button):
        mark, self._open_mark = self._open_mark, None
        entry = next((e for e in self.notes.entries if e.id == mark), None) if self.notes else None
        self.mark_popover.popdown()
        if not entry:
            return
        self.notes_panel.flush()  # what was just typed counts as a note
        entry = next((e for e in self.notes.entries if e.id == mark), entry)
        if entry.has_note:
            self._ask_remove_mark(entry)
        else:
            self._remove_mark(entry)

    def _ask_remove_mark(self, entry):
        count = len(entry.notes)
        dialog = Adw.AlertDialog(
            heading=_("Remove Mark and Notes?"),
            body=ngettext(
                "The mark and its note will be removed. You can bring them back with Ctrl+Z as long as the book is open.",
                "The mark and its {count} notes will be removed. You can bring them back with Ctrl+Z as long as the book is open.",
                count,
            ).format(count=count),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            self.remove_dialog = None
            # Looked up again: the file may have changed elsewhere while the question was open.
            current = next((e for e in self.notes.entries if e.id == entry.id), None) if self.notes else None
            if response == "remove" and current:
                self.notes_panel.flush()
                self._remove_mark(current)
        dialog.connect("response", respond)
        self.remove_dialog = dialog
        dialog.present(self)

    def _remove_mark(self, entry):
        try:
            self.notes.remove(entry.id)
        except NotesError:
            self._save_failed()
        if not any(e.cfi == entry.cfi for e in self.notes.entries):
            self._js(f"endpaper.dropMark({json.dumps(entry.cfi)})")
        self._notes_changed()

    def _on_popover_add_note(self, _button):
        mark, self._open_mark = self._open_mark, None
        self.mark_popover.popdown()
        if mark:
            self.show_mark_notes(mark, new_note=True)

    def _on_popover_closed(self, _popover):
        self._open_mark = None
        if not self.notes_split.get_show_sidebar():
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
        self.notes_panel.set_notes(self.notes)

    def _on_notes_changed(self, *_args):
        if not self.notes:
            return
        try:
            self.notes_panel.flush()  # what was just typed goes first, merged with the change
            if self.notes.refresh():
                self._send_marks()
                self._notes_changed()
        except NotesError:
            pass  # the next change or look tries again; nothing is written over an unread file

    def _close_notes(self):
        self.mark_popover.popdown()
        if self._notes_monitor:
            self._notes_monitor.cancel()
            self._notes_monitor = None
        self._flush_notes()
        self.notes = None
        self.notes_panel.set_notes(None)
        self.notes_button.set_active(False)

    def _flush_notes(self):
        if self.notes:
            self.notes_panel.flush()
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
        for split in (self.split, self.notes_split):
            # Without this a sidebar pops open whenever the window becomes wide again.
            if narrow and not split.get_collapsed():
                split.was_open = split.get_show_sidebar()
            split.set_collapsed(narrow)
            split.set_show_sidebar(False if narrow else getattr(split, "was_open", False))

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
        group.add(self._combo(_("Layout"), [("paginated", _("Pages")), ("scrolled", _("Continuous Scrolling"))], "flow"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(320, -1)
        box.append(self._build_themes())
        box.append(group)
        popover = Gtk.Popover(child=box)
        popover.connect("closed", self._on_popover_closed)
        return popover

    def _build_themes(self) -> Gtk.Widget:
        row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=6)
        self.theme_buttons, self._theme_guard = {}, False
        group = None
        for name in THEME_COLORS:
            title = _theme_name(name)
            # Translators: shown on the buttons for the colors of the pages, as a sample of the text
            button = Gtk.ToggleButton(label=_("Aa"), tooltip_text=title, css_classes=["theme-choice", f"theme-{name}"], group=group)
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            button.connect("toggled", self._on_theme_button, name)
            self.theme_buttons[name] = button
            row.append(button)
            group = group or button
        self.theme_follow = Adw.SwitchRow(title=_("Follow the System"), subtitle=_("Light or dark, as the system is set"))
        self.theme_follow.connect("notify::active", self._on_theme_follow)
        list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        list_box.append(self.theme_follow)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(row)
        box.append(list_box)
        self._show_theme()
        return box

    def _show_theme(self):
        self._theme_guard = True
        self.theme_follow.set_active(self.settings["theme"] == "auto")
        self.theme_buttons[self._resolved_settings()["theme"]].set_active(True)
        self._theme_guard = False

    def _on_theme_button(self, button, name):
        if not self._theme_guard and button.get_active():
            self.settings["theme"] = name
            self._apply_settings()

    def _on_theme_follow(self, row, _param):
        if not self._theme_guard:
            self.settings["theme"] = "auto" if row.get_active() else self._resolved_settings()["theme"]
            self._apply_settings()

    def _on_notes_only(self, only: bool):
        self.settings["notes_only"] = only
        config.update(reader=self.settings)

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
        self._show_theme()
        resolved = self._resolved_settings()
        color = Gdk.RGBA()
        color.parse(THEME_COLORS[resolved["theme"]][0])
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
        elif kind == "mark-none":
            self.show_message(_("There is no mark at this word."))
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
