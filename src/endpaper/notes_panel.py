"""The flyout beside the text: all marks of a book by chapter, each with any number of notes."""

from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk

from .library import conflict_paths_for
from .markdown_view import MarkdownView
from .notes import DEFAULT_MARK_COLOR, MARK_COLORS, NotesError

SAVE_DELAY_MS = 500
QUOTE_LINES = 4


class _NoteRow(Gtk.Box):
    def __init__(self, panel, entry_id: str, note=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["note-row"])
        self.panel, self.entry_id = panel, entry_id
        self.note_id = note.id if note else None
        self._timer = 0
        if note and note.conflict:
            self.append(Gtk.Label(
                label=_("Second version, written while the first changed elsewhere"),
                xalign=0, wrap=True, css_classes=["caption", "warning"],
            ))
        row = Gtk.Box(spacing=4)
        self.view = MarkdownView(note.text if note else "")
        self.view.set_hexpand(True)
        self.view.update_property([Gtk.AccessibleProperty.LABEL], [_("Note")])
        self.view.get_buffer().connect("changed", self._on_changed)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: self.save())
        self.view.add_controller(focus)
        row.append(self.view)
        self.remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", tooltip_text=_("Remove Note"), valign=Gtk.Align.START, css_classes=["flat", "circular"],
        )
        self.remove_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove Note")])
        self.remove_button.connect("clicked", lambda _b: panel.ask_remove_row(self))
        row.append(self.remove_button)
        self.append(row)

    def _on_changed(self, _buffer):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(SAVE_DELAY_MS, self._on_timer)

    def _on_timer(self):
        self._timer = 0
        self.save()
        return GLib.SOURCE_REMOVE

    @property
    def unsaved(self) -> bool:
        return bool(self._timer)

    def save(self):
        """Saved while typing. A field that became empty takes its note away; typing brings it back."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.panel.save_row(self)

    def forget_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0


class NotesPanel(Gtk.Box):
    """on_jump(cfi): show this place in the book. on_mark_changed(entry): a mark looks different now.
    on_save_failed(): tell the reader. on_only_notes(active): remember the switch."""

    def __init__(self, on_jump, on_mark_changed, on_save_failed, only_notes=False, on_only_notes=lambda _active: None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_jump, self.on_mark_changed, self.on_save_failed = on_jump, on_mark_changed, on_save_failed
        self.on_only_notes = on_only_notes
        self.shown_anyway: set[str] = set()  # asked for by name, even if the switch would hide it
        self.remove_dialog = None
        self.notes = None
        self.rows: list[_NoteRow] = []
        self.cards: dict[str, Gtk.Box] = {}
        self.dirty = True
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )
        self.viewport = Gtk.Viewport(child=self.content)
        scroller = Gtk.ScrolledWindow(child=self.viewport, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.empty = Adw.StatusPage(
            icon_name="document-edit-symbolic", title=_("No Marks Yet"),
            description=_("Choose a color below the text, then click the first and the last word."),
            css_classes=["compact"], vexpand=True,
        )
        self.only_notes = Adw.SwitchRow(title=_("Only Marks with Notes"), active=only_notes)
        self.only_notes.connect("notify::active", self._on_only_notes)
        self.filter_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"], margin_top=12, margin_start=12, margin_end=12,
        )
        self.filter_box.append(self.only_notes)
        self.append(self.filter_box)
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty, "empty")
        self.append(self.stack)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_("Notes")])

    def _on_only_notes(self, row, _param):
        self.shown_anyway.clear()
        self.rebuild()
        self.on_only_notes(row.get_active())

    def set_notes(self, notes) -> None:
        self.flush()
        self.notes = notes
        self.shown_anyway.clear()
        self.rebuild()

    def flush(self) -> None:
        for row in self.rows:
            if row.unsaved:
                row.save()

    def rebuild(self) -> None:
        self.flush()
        focused = next(((r.entry_id, r.note_id, r.view.get_buffer().props.cursor_position) for r in self.rows if r.view.get_root() and r.view.get_root().get_focus() is r.view), None)
        for row in self.rows:
            row.forget_timer()
        self.rows, self.cards = [], {}
        while child := self.content.get_first_child():
            self.content.remove(child)
        self.dirty = False
        self.filter_box.set_visible(bool(self.notes and self.notes.entries))
        if not self.notes:
            self._show_empty()
            return
        chapter = object()
        for entry in self.notes.entries:
            if self.only_notes.get_active() and not entry.has_note and entry.id not in self.shown_anyway:
                continue
            if entry.chapter != chapter:
                chapter = entry.chapter
                if chapter:
                    self.content.append(Gtk.Label(label=chapter, xalign=0, wrap=True, css_classes=["heading"], margin_top=8))
            self._add_card(entry)
        self._add_loose()
        if self.content.get_first_child() is None:
            self._show_empty()
        else:
            self.stack.set_visible_child_name("list")
        if focused:
            for row in self.rows:
                if (row.entry_id, row.note_id) == focused[:2]:
                    row.view.grab_focus()
                    buffer = row.view.get_buffer()
                    buffer.place_cursor(buffer.get_iter_at_offset(focused[2]))

    def _show_empty(self) -> None:
        if self.notes and self.notes.entries:
            self.empty.set_title(_("No Notes Yet"))
            self.empty.set_description(_("Click a mark in the text to add a note to it."))
        else:
            self.empty.set_title(_("No Marks Yet"))
            self.empty.set_description(_("Choose a color below the text, then click the first and the last word."))
        self.stack.set_visible_child_name("empty")

    def _add_card(self, entry) -> None:
        color = entry.color if entry.color in MARK_COLORS else DEFAULT_MARK_COLOR
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card", "note-card", f"note-tint-{color}"])
        label = Gtk.Label(
            label=entry.quote or _("Mark without text"), xalign=0, wrap=True, lines=QUOTE_LINES, ellipsize=3,
            css_classes=["note-quote"],
        )
        jump = Gtk.Button(child=label, tooltip_text=_("Go to This Place"), css_classes=["flat"])
        jump.connect("clicked", lambda _b, cfi=entry.cfi: self.on_jump(cfi))
        card.append(jump)
        for note in entry.notes:
            self._add_row(card, _NoteRow(self, entry.id, note))
        add = Gtk.Button(label=_("Add Note"), halign=Gtk.Align.START, css_classes=["flat", "note-add"])
        add.connect("clicked", lambda _b, entry_id=entry.id: self.add_empty_row(entry_id))
        card.add_button = add
        card.append(add)
        self.cards[entry.id] = card
        self.content.append(card)

    def _add_row(self, card, row: _NoteRow) -> None:
        self.rows.append(row)
        add = getattr(card, "add_button", None)
        card.insert_child_after(row, add.get_prev_sibling()) if add else card.append(row)

    def _add_loose(self) -> None:
        loose = self.notes.loose
        conflicts = conflict_paths_for(self.notes.book_path)
        if not loose and not conflicts:
            return
        self.content.append(Gtk.Label(label=_("Not Assigned to a Mark"), xalign=0, css_classes=["heading"], margin_top=8))
        for text in loose:
            self.content.append(Gtk.Label(label=text, xalign=0, wrap=True, selectable=True, css_classes=["card", "note-loose"]))
        for path in conflicts:
            self.content.append(Gtk.Label(
                label=_("More notes are in the file {name}").format(name=path.name), xalign=0, wrap=True, selectable=True,
                css_classes=["caption", "warning"],
            ))

    def add_empty_row(self, entry_id: str) -> None:
        card = self.cards.get(entry_id)
        if not card:
            return
        row = _NoteRow(self, entry_id)
        self._add_row(card, row)
        row.view.grab_focus()

    def show_entry(self, entry_id: str, new_note: bool = False) -> None:
        """Shows a mark in the flyout, with the cursor in a new note if one is wanted."""
        self.shown_anyway.add(entry_id)
        if self.dirty or entry_id not in self.cards:
            self.rebuild()
        card = self.cards.get(entry_id)
        if not card:
            return
        if new_note:
            self.add_empty_row(entry_id)
        else:
            card.get_first_child().grab_focus()  # to read: a note is only for writing once it is clicked
        GLib.idle_add(self._scroll_to, card)

    def _scroll_to(self, card):
        if card.get_parent() is self.content:
            self.viewport.scroll_to(card, None)
        return GLib.SOURCE_REMOVE

    def save_row(self, row: _NoteRow) -> None:
        if not self.notes:
            return
        text = row.view.text.strip()
        entry = next((e for e in self.notes.entries if e.id == row.entry_id), None)
        if entry is None:
            return  # taken away elsewhere with no change here: the text in the file is the last word
        note = entry.note(row.note_id) if row.note_id else None
        had_note = entry.has_note
        try:
            if text and note is None:
                row.note_id = self.notes.add_note(entry.id, text).id
            elif text and note.text != text:
                self.notes.update_note(entry.id, note.id, text)
            elif not text and note is not None:
                self.notes.remove_note(entry.id, note.id)
                row.note_id = None
        except NotesError:
            self.on_save_failed()
        except KeyError:
            return
        changed = next((e for e in self.notes.entries if e.id == row.entry_id), None)
        if changed and changed.has_note != had_note:
            self.on_mark_changed(changed)

    def ask_remove_row(self, row: _NoteRow) -> None:
        row.save()
        if not row.note_id:  # nothing written yet: only the empty field goes
            self.remove_row(row)
            return
        ids = row.entry_id, row.note_id
        dialog = Adw.AlertDialog(
            heading=_("Remove Note?"), body=_("You can bring it back with Ctrl+Z as long as the book is open."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            self.remove_dialog = None
            # The list may have been built anew meanwhile, because the file changed elsewhere.
            current = next((r for r in self.rows if (r.entry_id, r.note_id) == ids), None)
            if response == "remove" and current:
                self.remove_row(current)
        dialog.connect("response", respond)
        self.remove_dialog = dialog
        dialog.present(self)

    def remove_row(self, row: _NoteRow) -> None:
        row.save()  # the last word typed comes back too, if this is undone
        entry = next((e for e in self.notes.entries if e.id == row.entry_id), None) if self.notes else None
        if entry and row.note_id and entry.note(row.note_id):
            had_note = entry.has_note
            try:
                self.notes.remove_note(entry.id, row.note_id, undoable=True)
            except NotesError:
                self.on_save_failed()
            changed = next((e for e in self.notes.entries if e.id == entry.id), None)
            if changed and changed.has_note != had_note:
                self.on_mark_changed(changed)
        row.forget_timer()
        self.rows.remove(row)
        row.get_parent().remove(row)
