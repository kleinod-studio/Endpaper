"""The bookshelf: categories that fold, books with their covers, and everything to keep them in order."""

from gettext import gettext as _
from gettext import ngettext
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from . import config, library
from .book_info import BookInfo, BookInfoStore
from .library import Book, Category, LibraryError

COVER_WIDTH = 130
COVER_HEIGHT = 190
REFRESH_DELAY_MS = 400


def error_text(error: Exception) -> str:
    if isinstance(error, LibraryError):
        if error.code == "exists":
            return _("“{name}” already exists.").format(name=error.name)
        if error.code == "invalid-name":
            return _("This name cannot be used.")
    return _("That did not work: {reason}").format(reason=error)


class BookCard(Gtk.FlowBoxChild):
    def __init__(self, shelf: "ShelfPage", book: Book):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.shelf = shelf
        self.book = book
        self.info = BookInfo(title=book.path.stem)
        self.pinned = False

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self.placeholder = Gtk.Label(
            wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, justify=Gtk.Justification.CENTER, lines=6,
            ellipsize=Pango.EllipsizeMode.END, margin_start=10, margin_end=10,
        )
        self.placeholder.add_css_class("dim-label")
        self.placeholder.add_css_class("caption")
        self.placeholder.set_max_width_chars(14)

        stack = Gtk.Overlay(child=self.placeholder)
        stack.add_overlay(self.picture)
        stack.set_size_request(COVER_WIDTH, COVER_HEIGHT)
        stack.set_overflow(Gtk.Overflow.HIDDEN)
        stack.add_css_class("card")
        self.cover_frame = stack

        self.progress = Gtk.ProgressBar(valign=Gtk.Align.END, visible=False)
        self.progress.add_css_class("osd")
        stack.add_overlay(self.progress)

        self.pin_button = Gtk.Button(
            icon_name="view-pin-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.START,
            margin_top=6, margin_end=6, visible=False,
        )
        self.pin_button.add_css_class("circular")
        self.pin_button.add_css_class("osd")
        self.pin_button.connect("clicked", lambda _b: self.shelf.toggle_pin(self))
        stack.add_overlay(self.pin_button)

        self.title_label = Gtk.Label(
            xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=14,
        )
        self.title_label.set_size_request(COVER_WIDTH, -1)
        self.author_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=14)
        self.author_label.add_css_class("dim-label")
        self.author_label.add_css_class("caption")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(stack)
        box.append(self.title_label)
        box.append(self.author_label)
        box.set_size_request(COVER_WIDTH, -1)
        self.set_child(box)

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_a: self._set_hover(True))
        hover.connect("leave", lambda *_a: self._set_hover(False))
        self.add_controller(hover)
        self._hover = False

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.add_controller(click)

        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare)
        drag.connect("drag-begin", self._on_drag_begin)
        self.add_controller(drag)

        shortcuts = Gtk.ShortcutController()
        for trigger, callback in (
            ("Menu|<Shift>F10", lambda *_a: self.show_menu() or True),
            ("F2", lambda *_a: self.shelf.rename_book(self) or True),
            ("Delete", lambda *_a: self.shelf.trash_book(self) or True),
            ("<Primary>p", lambda *_a: self.shelf.toggle_pin(self) or True),
            ("<Alt>Up", lambda *_a: self.shelf.move_to_neighbor(self, -1) or True),
            ("<Alt>Down", lambda *_a: self.shelf.move_to_neighbor(self, 1) or True),
        ):
            shortcuts.add_shortcut(
                Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger), action=Gtk.CallbackAction.new(callback))
            )
        self.add_controller(shortcuts)
        self.update_display()

    def update_display(self):
        self.title_label.set_label(self.info.title)
        self.placeholder.set_label(self.info.title)
        author = self.info.author_text if self.info.readable else _("Cannot be read")
        self.author_label.set_label(author)
        self.author_label.set_visible(bool(author))
        self.set_tooltip_text(f"{self.info.title}\n{author}" if author else self.info.title)
        if self.info.cover:
            self.picture.set_filename(self.info.cover)
        self.picture.set_visible(bool(self.info.cover))
        self.pin_button.set_visible(self.pinned or self._hover)
        self.pin_button.set_opacity(1 if self.pinned or self._hover else 0)
        self.pin_button.set_tooltip_text(_("Unpin") if self.pinned else _("Pin"))
        if self.pinned:
            self.pin_button.add_css_class("accent")
        else:
            self.pin_button.remove_css_class("accent")
        fraction = self.shelf.state.fraction(self.info.key) if self.info.key else None
        self.progress.set_visible(bool(fraction))
        self.progress.set_fraction(fraction or 0)
        parts = [self.info.title]
        if author:
            parts.append(author)
        if self.pinned:
            parts.append(_("Pinned"))
        if fraction:
            parts.append(_("{percent}%").format(percent=round(fraction * 100)))
        self.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(parts)])

    def _set_hover(self, hover):
        self._hover = hover
        self.update_display()

    def set_info(self, info: BookInfo):
        self.info = info
        self.update_display()
        self.shelf.card_changed(self)

    def matches(self, query: str) -> bool:
        return not query or query in f"{self.info.title} {self.info.author_text} {self.book.path.stem}".casefold()

    def _on_right_click(self, gesture, _n, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.show_menu(x, y)

    def show_menu(self, x=None, y=None):
        popover = Gtk.PopoverMenu.new_from_model(self.shelf.book_menu(self))
        popover.set_parent(self)
        popover.set_has_arrow(False)
        if x is not None:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _on_drag_prepare(self, _source, _x, _y):
        return Gdk.ContentProvider.new_for_value(GObject.Value(str, str(self.book.path)))

    def _on_drag_begin(self, source, _drag):
        source.set_icon(Gtk.WidgetPaintable.new(self.cover_frame), COVER_WIDTH // 2, COVER_HEIGHT // 2)


class CategorySection(Gtk.Box):
    def __init__(self, shelf: "ShelfPage", category: Category):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.shelf = shelf
        self.category = category
        self.cards: dict[Path, BookCard] = {}
        self.key = category.name or ""

        self.chevron = Gtk.Image()
        self.heading = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.heading.add_css_class("title-3")
        self.count = Gtk.Label()
        self.count.add_css_class("dim-label")
        inner = Gtk.Box(spacing=8)
        inner.append(self.chevron)
        inner.append(self.heading)
        inner.append(self.count)
        self.toggle = Gtk.Button(child=inner, hexpand=True, halign=Gtk.Align.START)
        self.toggle.add_css_class("flat")
        self.toggle.connect("clicked", lambda _b: self.set_collapsed(not self.collapsed))

        menu = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=_("Category Menu"), valign=Gtk.Align.CENTER)
        menu.add_css_class("flat")
        menu.set_menu_model(shelf.category_menu(category))
        self.menu_button = menu
        header = Gtk.Box(spacing=6)
        header.append(self.toggle)
        header.append(menu)
        self.append(header)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True, homogeneous=False,
            column_spacing=18, row_spacing=18, min_children_per_line=1, max_children_per_line=40,
            valign=Gtk.Align.START,
        )
        self.flow.set_sort_func(self._sort)
        self.flow.set_filter_func(self._filter)
        self.flow.connect("child-activated", lambda _f, card: shelf.open_card(card))
        self.append(self.flow)

        self.empty_hint = Gtk.Label(
            label=_("No books here yet. Drag them here or add them from the menu."), xalign=0, wrap=True,
            margin_start=12, visible=False,
        )
        self.empty_hint.add_css_class("dim-label")
        self.append(self.empty_hint)

        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drop.set_gtypes([GObject.TYPE_STRING, Gdk.FileList])
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self.collapsed = self.key in shelf.state.collapsed
        self.update(category)

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gdk.FileList):
            self.shelf.add_files([f.get_path() for f in value.get_files() if f.get_path()], self.category.path)
            return True
        return self.shelf.move_dropped(str(value), self.category.path)

    def update(self, category: Category):
        self.category = category
        self.heading.set_label(category.name or _("Without Category"))
        present = {book.path for book in category.books}
        for path in [p for p in self.cards if p not in present]:
            self.flow.remove(self.cards.pop(path))
        for book in category.books:
            card = self.cards.get(book.path)
            if card is None:
                card = BookCard(self.shelf, book)
                self.cards[book.path] = card
                self.flow.append(card)
                self.shelf.info_store.request(book.path, card.set_info)
            else:
                card.book = book
            card.pinned = category.is_pinned(book)
            card.update_display()
        count = len(category.books)
        self.count.set_label(str(count))
        self.empty_hint.set_visible(count == 0)
        self.menu_button.set_menu_model(self.shelf.category_menu(category))
        self._update_toggle()
        self.refilter()

    def _update_toggle(self):
        self.chevron.set_from_icon_name("pan-end-symbolic" if self.collapsed else "pan-down-symbolic")
        count = len(self.category.books)
        name = self.category.name or _("Without Category")
        books = ngettext("{count} book", "{count} books", count).format(count=count)
        self.toggle.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}, {books}"])
        self.toggle.update_state([Gtk.AccessibleState.EXPANDED], [0 if self.collapsed else 1])
        self.toggle.set_tooltip_text(_("Unfold") if self.collapsed else _("Fold"))

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        if collapsed:
            self.shelf.state.collapsed.add(self.key)
        else:
            self.shelf.state.collapsed.discard(self.key)
        self.shelf.save_state()
        self._update_toggle()
        self.refilter()

    def _filter(self, card: BookCard) -> bool:
        query = self.shelf.query
        if query:
            return card.matches(query)
        return not self.collapsed or card.pinned  # pinned books stay in reach while the category is folded

    def _sort(self, a: BookCard, b: BookCard) -> int:
        if a.pinned != b.pinned:
            return -1 if a.pinned else 1
        mode = self.shelf.sort_mode
        if mode == "recent":
            read_a, read_b = self.shelf.state.last_read(a.info.key), self.shelf.state.last_read(b.info.key)
            if read_a != read_b:
                return -1 if read_a > read_b else 1
        if mode == "author":
            author_a, author_b = a.info.author_text.casefold(), b.info.author_text.casefold()
            if author_a != author_b:
                return -1 if author_a < author_b else 1
        title_a, title_b = a.info.title.casefold(), b.info.title.casefold()
        return 0 if title_a == title_b else (-1 if title_a < title_b else 1)

    def refilter(self):
        self.flow.invalidate_filter()
        self.flow.invalidate_sort()
        if self.shelf.query:
            self.set_visible(any(card.matches(self.shelf.query) for card in self.cards.values()))
        else:
            self.set_visible(True)


class ShelfPage(Adw.NavigationPage):
    """on_open(book, info): a book was chosen. toast(text): shows a short message."""

    def __init__(self, window, state: config.State, on_open, toast):
        super().__init__(title="Endpaper", tag="shelf")
        self.window = window
        self.state = state
        self.on_open = on_open
        self.toast = toast
        self.root: Path | None = None
        self.sections: dict[Path, CategorySection] = {}
        self.categories: list[Category] = []
        self.query = ""
        self.sort_mode = config.sort_mode()
        self.info_store = BookInfoStore()
        self.monitors: dict[str, Gio.FileMonitor] = {}
        self._refresh_timer = 0
        self._install_actions()
        self._build()

    # --- Layout -----------------------------------------------------------------------------

    def _build(self):
        header = Adw.HeaderBar()
        add_menu = Gio.Menu()
        add_menu.append(_("New Category…"), "shelf.new-category")
        add_menu.append(_("Add Books…"), "shelf.add-books")
        self.add_button = Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text=_("Add"), menu_model=add_menu)
        header.pack_start(self.add_button)

        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("Find a Book"))
        header.pack_start(self.search_button)

        primary = Gio.Menu()
        sort = Gio.Menu()
        sort.append(_("Title"), "shelf.sort::title")
        sort.append(_("Author"), "shelf.sort::author")
        sort.append(_("Recently Read"), "shelf.sort::recent")
        primary.append_section(_("Sort By"), sort)
        other = Gio.Menu()
        other.append(_("Choose Library Folder…"), "shelf.choose-library")
        other.append(_("Keyboard Shortcuts"), "win.show-shortcuts")
        other.append(_("About Endpaper"), "app.about")
        primary.append_section(None, other)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text=_("Main Menu"), menu_model=primary, primary=True))

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Title or author"), hexpand=True)
        self.search_entry.connect("search-changed", self._on_search)
        clamp = Adw.Clamp(child=self.search_entry, maximum_size=480)
        self.search_bar = Gtk.SearchBar(child=clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_button.bind_property(
            "active", self.search_bar, "search-mode-enabled", GObject.BindingFlags.BIDIRECTIONAL
        )

        self.sections_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=26, margin_top=18, margin_bottom=30, margin_start=18, margin_end=18,
        )
        self.scroller = Gtk.ScrolledWindow(
            child=Adw.Clamp(child=self.sections_box, maximum_size=1500, tightening_threshold=1200),
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        choose = Gtk.Button(label=_("Choose Library Folder…"), halign=Gtk.Align.CENTER)
        choose.add_css_class("pill")
        choose.add_css_class("suggested-action")
        choose.set_action_name("shelf.choose-library")
        no_library = Adw.StatusPage(
            icon_name="studio.kleinod.Endpaper-symbolic", title=_("Welcome to Endpaper"),
            description=_("Choose a folder for your books. Each of its subfolders becomes a category on your shelf."),
            child=choose,
        )
        add = Gtk.Button(label=_("Add Books…"), halign=Gtk.Align.CENTER)
        add.add_css_class("pill")
        add.add_css_class("suggested-action")
        add.set_action_name("shelf.add-books")
        empty = Adw.StatusPage(
            icon_name="studio.kleinod.Endpaper-symbolic", title=_("Your Shelf Is Empty"),
            description=_("Add EPUB books, or drag them here. They are copied into your library folder."), child=add,
        )
        no_results = Adw.StatusPage(icon_name="system-search-symbolic", title=_("No Books Found"))

        self.stack = Gtk.Stack()
        self.stack.add_named(no_library, "no-library")
        self.stack.add_named(empty, "empty")
        self.stack.add_named(no_results, "no-results")
        self.stack.add_named(self.scroller, "shelf")

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: self.add_files(
            [f.get_path() for f in value.get_files() if f.get_path()], self.root) or True)
        self.stack.add_controller(drop)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        self.set_child(view)

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        for name, callback, parameter in (
            ("new-category", self._action_new_category, None),
            ("add-books", self._action_add_books, None),
            ("choose-library", self._action_choose_library, None),
            ("add-to-category", self._action_add_to_category, "s"),
            ("rename-category", self._action_rename_category, "s"),
            ("trash-category", self._action_trash_category, "s"),
            ("open", lambda _a, p: self._with_card(p, self.open_card), "s"),
            ("toggle-pin", lambda _a, p: self._with_card(p, self.toggle_pin), "s"),
            ("rename-book", lambda _a, p: self._with_card(p, self.rename_book), "s"),
            ("trash-book", lambda _a, p: self._with_card(p, self.trash_book), "s"),
            ("move-book", self._action_move_book, "as"),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType(parameter) if parameter else None)
            action.connect("activate", callback)
            group.add_action(action)
        sort = Gio.SimpleAction.new_stateful("sort", GLib.VariantType("s"), GLib.Variant("s", self.sort_mode))
        sort.connect("activate", self._action_sort)
        group.add_action(sort)
        self.insert_action_group("shelf", group)
        self.actions = group

    # --- Library -----------------------------------------------------------------------------

    def set_root(self, root: Path | None):
        self.root = root
        for section in self.sections.values():
            self.sections_box.remove(section)
        self.sections = {}
        self.refresh()

    def refresh(self):
        self._refresh_timer = 0
        if self.root is None or not self.root.is_dir():
            self.stack.set_visible_child_name("no-library")
            self.categories = []
            self._watch([])
            return GLib.SOURCE_REMOVE
        self.categories = library.scan(self.root)
        present = {c.path for c in self.categories}
        for path in [p for p in self.sections if p not in present]:
            self.sections_box.remove(self.sections.pop(path))
        previous = None
        for category in self.categories:
            section = self.sections.get(category.path)
            if section is None:
                section = CategorySection(self, category)
                self.sections[category.path] = section
                self.sections_box.append(section)
            else:
                section.update(category)
            self.sections_box.reorder_child_after(section, previous)
            previous = section
        self._watch(self._folders_to_watch())
        self._update_stack()
        return GLib.SOURCE_REMOVE

    def _folders_to_watch(self) -> list[Path]:
        folders = [self.root]
        for category in self.categories:
            if category.name is not None:
                folders.append(category.path)
            folders.extend(book.path.parent for book in category.books)
        return folders

    def _watch(self, folders: list[Path]):
        wanted = {str(f) for f in folders}
        for path in [p for p in self.monitors if p not in wanted]:
            self.monitors.pop(path).cancel()
        for path in wanted - self.monitors.keys():
            try:
                monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            monitor.connect("changed", self._on_folder_changed)
            self.monitors[path] = monitor

    def _on_folder_changed(self, _monitor, file, _other, _event):
        if file.get_basename().endswith(".tmp"):
            return  # our own atomic saves, and half-finished copies
        self.schedule_refresh()

    def schedule_refresh(self):
        if not self._refresh_timer:
            self._refresh_timer = GLib.timeout_add(REFRESH_DELAY_MS, self.refresh)

    def _update_stack(self):
        has_books = any(c.books for c in self.categories)
        if not self.categories:
            self.stack.set_visible_child_name("empty")
        elif self.query and not any(s.get_visible() for s in self.sections.values()):
            self.stack.set_visible_child_name("no-results")
        else:
            self.stack.set_visible_child_name("shelf" if has_books or self.categories else "empty")

    def save_state(self):
        try:
            self.state.save()
        except OSError:
            pass

    def card_changed(self, card: BookCard):
        card.get_parent() and card.get_parent().invalidate_sort()
        if self.query:
            self._apply_query()

    def refresh_progress(self):
        """After reading: the progress bars and, when sorted by recent reading, the order."""
        for section in self.sections.values():
            for card in section.cards.values():
                card.update_display()
            section.flow.invalidate_sort()

    # --- Search and sort --------------------------------------------------------------------

    def start_search(self):
        self.search_button.set_active(True)
        self.search_entry.grab_focus()

    def _on_search(self, entry):
        self.query = entry.get_text().strip().casefold()
        self._apply_query()

    def _apply_query(self):
        for section in self.sections.values():
            section.refilter()
        self._update_stack()

    def _action_sort(self, action, value):
        action.set_state(value)
        self.sort_mode = value.get_string()
        config.update(sort=self.sort_mode)
        for section in self.sections.values():
            section.flow.invalidate_sort()

    # --- Menus -----------------------------------------------------------------------------

    def category_menu(self, category: Category) -> Gio.Menu:
        target = GLib.Variant("s", str(category.path))
        menu = Gio.Menu()

        def add(label, action):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, target)
            menu.append_item(item)
        add(_("Add Books…"), "shelf.add-to-category")
        if category.name is not None:
            add(_("Rename…"), "shelf.rename-category")
            add(_("Move to Trash"), "shelf.trash-category")
        return menu

    def book_menu(self, card: BookCard) -> Gio.Menu:
        target = GLib.Variant("s", str(card.book.path))
        menu = Gio.Menu()

        def add(section, label, action):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, target)
            section.append_item(item)
        first = Gio.Menu()
        add(first, _("Open"), "shelf.open")
        add(first, _("Unpin") if card.pinned else _("Pin"), "shelf.toggle-pin")
        menu.append_section(None, first)
        destinations = Gio.Menu()
        for category in self.categories:
            if category.path != card.book.category:
                item = Gio.MenuItem.new(category.name or _("Without Category"), None)
                item.set_action_and_target_value(
                    "shelf.move-book", GLib.Variant("as", [str(card.book.path), str(category.path)])
                )
                destinations.append_item(item)
        second = Gio.Menu()
        if destinations.get_n_items():
            second.append_submenu(_("Move To"), destinations)
        add(second, _("Rename…"), "shelf.rename-book")
        add(second, _("Move to Trash"), "shelf.trash-book")
        menu.append_section(None, second)
        return menu

    # --- Actions on books ----------------------------------------------------------------

    def _card_for(self, path: str) -> BookCard | None:
        for section in self.sections.values():
            if card := section.cards.get(Path(path)):
                return card
        return None

    def _with_card(self, parameter, callback):
        if card := self._card_for(parameter.get_string()):
            callback(card)

    def open_card(self, card: BookCard):
        if not card.info.readable:
            self.toast(_("This file could not be opened as a book."))
            return
        self.on_open(card.book, card.info)

    def _attempt(self, operation) -> bool:
        try:
            operation()
        except (LibraryError, OSError) as error:
            self.toast(error_text(error))
            return False
        self.schedule_refresh()
        return True

    def toggle_pin(self, card: BookCard):
        self._attempt(lambda: library.set_pinned(card.book, not card.pinned))

    def _action_move_book(self, _action, parameter):
        path, category = parameter.unpack()
        if card := self._card_for(path):
            self.move_card(card, Path(category))

    def move_card(self, card: BookCard, category: Path):
        self._attempt(lambda: library.move_book(card.book, category))

    def move_to_neighbor(self, card: BookCard, step: int):
        paths = [c.path for c in self.categories]
        try:
            index = paths.index(card.book.category) + step
        except ValueError:
            return
        if 0 <= index < len(paths):
            self.move_card(card, paths[index])

    def move_dropped(self, path: str, category: Path) -> bool:
        card = self._card_for(path)  # only books of this shelf; a drag can carry any text
        if card is None:
            return False
        if card.book.category != category:
            self.move_card(card, category)
        return True

    def rename_book(self, card: BookCard):
        self._ask_name(
            _("Rename Book"), card.book.path.stem, _("Rename"),
            lambda name: self._attempt(lambda: library.rename_book(card.book, name)),
        )

    def trash_book(self, card: BookCard):
        paths = library.trash_paths_for_book(card.book)
        title = card.info.title
        self._trash(paths, _("“{title}” was moved to the trash.").format(title=title))

    def _trash(self, paths: list[Path], message: str):
        """Asynchronous, as it can take a while; never falls back to deleting for good."""
        def finished(file, result, remaining):
            try:
                file.trash_finish(result)
            except GLib.Error as error:
                self.toast(_("Could not move to the trash: {reason}").format(reason=error.message))
                self.schedule_refresh()
                return
            if remaining:
                following = Gio.File.new_for_path(str(remaining[0]))
                following.trash_async(GLib.PRIORITY_DEFAULT, None, finished, remaining[1:])
            else:
                self.toast(message)
                self.schedule_refresh()
        Gio.File.new_for_path(str(paths[0])).trash_async(GLib.PRIORITY_DEFAULT, None, finished, paths[1:])

    # --- Actions on categories ------------------------------------------------------------

    def _action_new_category(self, *_a):
        if self.root:
            self._ask_name(
                _("New Category"), "", _("Create"),
                lambda name: self._attempt(lambda: library.create_category(self.root, name)),
            )

    def _action_rename_category(self, _action, parameter):
        path = Path(parameter.get_string())

        def rename(name):
            old = path.name
            if self._attempt(lambda: library.rename_category(path, name)) and old in self.state.collapsed:
                self.state.collapsed.discard(old)
                self.state.collapsed.add(name.strip())
                self.save_state()
        self._ask_name(_("Rename Category"), path.name, _("Rename"), rename)

    def _action_trash_category(self, _action, parameter):
        path = Path(parameter.get_string())
        count = sum(len(c.books) for c in self.categories if c.path == path)
        dialog = Adw.AlertDialog(
            heading=_("Move “{name}” to the Trash?").format(name=path.name),
            body=ngettext(
                "The folder goes to the trash with {count} book. You can restore it from there.",
                "The folder goes to the trash with its {count} books. You can restore it from there.",
                count,
            ).format(count=count),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("trash", _("Move to Trash"))
        dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")

        def respond(_dialog, response):
            if response == "trash":
                self._trash([path], _("“{title}” was moved to the trash.").format(title=path.name))
        dialog.connect("response", respond)
        dialog.present(self.window)

    def _ask_name(self, heading: str, initial: str, action: str, callback):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Adw.EntryRow(title=_("Name"), text=initial, activates_default=True)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("boxed-list")
        box.append(entry)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", action)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("ok", bool(initial.strip()))
        entry.connect("changed", lambda e: dialog.set_response_enabled("ok", bool(e.get_text().strip())))

        def respond(_dialog, response):
            if response == "ok":
                callback(entry.get_text())
        dialog.connect("response", respond)
        dialog.present(self.window)
        entry.grab_focus()

    # --- Adding books ----------------------------------------------------------------------

    def _book_filter(self) -> Gio.ListStore:
        epub_filter = Gtk.FileFilter(name=_("EPUB Books"))
        epub_filter.add_suffix("epub")
        epub_filter.add_mime_type("application/epub+zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(epub_filter)
        return filters

    def _action_add_books(self, *_a):
        if self.root:
            self._choose_files(self._choose_category_then_add)

    def _action_add_to_category(self, _action, parameter):
        category = Path(parameter.get_string())
        self._choose_files(lambda paths: self.add_files(paths, category))

    def _choose_files(self, callback):
        dialog = Gtk.FileDialog(title=_("Add Books"), filters=self._book_filter())

        def done(source, result):
            try:
                files = source.open_multiple_finish(result)
            except GLib.Error:
                return  # cancelled
            callback([f.get_path() for f in files if f.get_path()])
        dialog.open_multiple(self.window, None, done)

    def _choose_category_then_add(self, paths: list[str]):
        folders = [c for c in self.categories if c.name is not None]
        if not folders:
            self.add_files(paths, self.root)
            return
        dialog = Adw.AlertDialog(heading=_("Add to Which Category?"))
        choices = [(_("Without Category"), self.root)] + [(c.name, c.path) for c in folders]
        dropdown = Gtk.DropDown(model=Gtk.StringList.new([label for label, _path in choices]))
        dialog.set_extra_child(dropdown)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response == "add":
                self.add_files(paths, choices[dropdown.get_selected()][1])
        dialog.connect("response", respond)
        dialog.present(self.window)

    def add_files(self, paths: list[str], category: Path | None):
        if category is None:
            return
        problems = 0
        added = 0
        for path in paths:
            source = Path(path)
            if not source.name.lower().endswith(library.BOOK_SUFFIX):
                problems += 1
                continue
            try:
                library.add_book(source, category)
                added += 1
            except (LibraryError, OSError) as error:
                problems += 1
                self.toast(error_text(error))
        if added:
            self.toast(ngettext("{count} book added.", "{count} books added.", added).format(count=added))
        elif problems and not added and len(paths) > 0:
            pass  # the reason was shown above, or the files were not books
        self.schedule_refresh()

    # --- Library folder --------------------------------------------------------------------

    def _action_choose_library(self, *_a):
        dialog = Gtk.FileDialog(title=_("Choose Library Folder"))

        def done(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return  # cancelled
            if folder.get_path():
                config.update(library_path=folder.get_path())
                self.set_root(Path(folder.get_path()))
        dialog.select_folder(self.window, None, done)
