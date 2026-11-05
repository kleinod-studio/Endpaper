"""Drives the real user interface automatically. Needs a running desktop session.

Run: python3 tests/gui_smoke.py
Uses a temporary folder and its own configuration; none of your data or books are touched."""

import atexit
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [os.environ.get("ENDPAPER_CODE", str(ROOT / "src")), str(ROOT / "tests")]

# Inside the home directory so that the system trash works (it often doesn't on /tmp).
TMP = Path(tempfile.mkdtemp(prefix="endpaper-smoke-", dir=Path.home() / ".cache"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)  # also catches a late save at exit
LIB = TMP / "lib"
LIB.mkdir()
for name, value in (("XDG_CONFIG_HOME", "cfg"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[name] = str(TMP / value)
    (TMP / value).mkdir()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

from endpaper import config, library  # noqa: E402
from endpaper.main import EndpaperApplication  # noqa: E402
from endpaper.notes import MARK_COLORS, NotesFile, NotesKept
from make_epub import make_epub, make_figures_epub, make_hostile_epub, make_answers_epub, make_notes_epub, make_styled_epub  # noqa: E402

failures = []
passed = 0


def check(condition, what):
    global passed
    if condition:
        passed += 1
        print(f"  ok    {what}")
    else:
        failures.append(what)
        print(f"  FAIL  {what}")


def spin(seconds):
    context, end = GLib.MainContext.default(), time.time() + seconds
    while time.time() < end:
        context.iteration(False) or time.sleep(0.003)


def wait_for(condition, seconds=25):
    context, end = GLib.MainContext.default(), time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        context.iteration(False) or time.sleep(0.003)
    return False


class Canary(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Canary.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *_args):
        pass


def js(reader, script):
    box = {}
    reader._js(script, lambda value: box.update(done=True, value=value))
    wait_for(lambda: box.get("done"), 15)
    return box.get("value")


def remove_note(row):
    """The trash button, then the answer in the dialog that asks first."""
    panel = row.panel
    row.remove_button.emit("clicked")
    dialog = panel.remove_dialog
    if dialog:
        dialog.emit("response", "remove")
        dialog.force_close()


def scenario(app, window):
    shelf = window.shelf
    state = window.state

    print("Library folder")
    check(shelf.stack.get_visible_child_name() == "no-library", "without a folder, it asks for one")
    config.update(library_path=str(LIB))
    shelf.set_root(LIB)
    check(shelf.stack.get_visible_child_name() == "empty", "an empty folder shows the empty shelf")

    print("Shelf")
    romane, studium = LIB / "Romane", LIB / "Studium"
    romane.mkdir()
    studium.mkdir()
    make_epub(romane / "b.epub", title="Bravo", author="Zed", identifier="urn:b")
    make_epub(romane / "a.epub", title="Alpha", author="Yann", identifier="urn:a")
    make_epub(romane / "c.epub", title="Charlie", author="Xena", identifier="urn:c", cover=False)
    make_epub(studium / "d.epub", title="Delta", identifier="urn:d")
    make_epub(LIB / "lose.epub", title="Lose", identifier="urn:lose")
    (romane / "kaputt.epub").write_bytes(b"not a book")
    check(wait_for(lambda: len(shelf.sections) == 3), "new folders and books appear without a restart")
    section = shelf.sections[romane]
    check(wait_for(lambda: len(section.cards) == 4), "the category shows its books")
    card = lambda name, s=None: (s or shelf.sections[romane]).cards[romane / name]
    check(wait_for(lambda: card("a.epub").info.title == "Alpha"), "title comes from the book")
    check(wait_for(lambda: card("a.epub").info.cover is not None), "cover is read")
    check(wait_for(lambda: not card("kaputt.epub").info.readable), "a broken file is marked as unreadable")
    check(wait_for(lambda: card("c.epub").info.title == "Charlie") and card("c.epub").info.cover is None, "a book without a cover works")

    print("Pins and folding")
    target = card("b.epub")
    shelf.toggle_pin(target)
    check(wait_for(lambda: target.pinned), "pinning shows up")
    check(library.read_pins(romane) == ["b.epub"], "the pin is stored in the category folder")
    spin(0.3)
    check(section.flow.get_child_at_index(0) is not None and section._sort(target, card("a.epub")) < 0, "pinned books come first")
    section.set_collapsed(True)
    spin(0.3)
    check(target.get_child_visible() and not card("a.epub").get_child_visible(), "a folded category still shows its pinned books")
    check("Romane" in config.State().collapsed, "folding is remembered")
    section.set_collapsed(False)
    spin(0.2)
    check(card("a.epub").get_child_visible(), "unfolding shows all books")

    print("Sorting and finding")
    shelf.sort_mode = "author"
    check(section._sort(card("c.epub"), card("a.epub")) < 0, "sorts by author")
    shelf.sort_mode = "title"
    shelf.search_entry.set_text("yann")
    check(wait_for(lambda: shelf.query == "yann"), "search takes the text")
    spin(0.2)
    check(card("a.epub").get_child_visible() and not card("c.epub").get_child_visible(), "search filters by author")
    check(not shelf.sections[studium].get_visible(), "categories without a match are hidden")
    shelf.search_entry.set_text("gibt es nicht")
    check(wait_for(lambda: shelf.stack.get_visible_child_name() == "no-results"), "no match says so")
    shelf.search_entry.set_text("")
    check(wait_for(lambda: shelf.stack.get_visible_child_name() == "shelf"), "clearing the search restores the shelf")

    print("Keeping order")
    notes = romane / "a.notes.md"
    notes.write_text("Meine Anmerkung", encoding="utf-8")
    shelf.move_card(card("a.epub"), studium)
    check(wait_for(lambda: (studium / "a.epub") in shelf.sections[studium].cards), "a book moves to another category")
    check((studium / "a.notes.md").read_text(encoding="utf-8") == "Meine Anmerkung" and not notes.exists(), "its notes move along")
    check(shelf.move_dropped("/etc/hostname", studium) is False, "a drop of something else is refused")
    check((romane / "b.epub").exists(), "nothing else moved")
    make_epub(romane / "a.epub", title="Doppelt", identifier="urn:dup")
    wait_for(lambda: (romane / "a.epub") in shelf.sections[romane].cards)
    original = (studium / "a.epub").read_bytes()
    shelf.move_card(shelf.sections[studium].cards[studium / "a.epub"], romane)
    spin(0.6)
    check((studium / "a.epub").read_bytes() == original, "moving never overwrites a book of the same name")
    shelf.move_card(card("c.epub"), LIB)
    check(wait_for(lambda: (LIB / "c.epub") in shelf.sections[LIB].cards), "a book can leave its category")
    (romane / "a.epub").unlink()
    wait_for(lambda: (romane / "a.epub") not in shelf.sections[romane].cards)

    # In the Flatpak the trash goes through a portal to the user's real trash: don't litter it with test files.
    use_trash = "FLATPAK_ID" not in os.environ
    trash = Path(os.environ["XDG_DATA_HOME"]) / "Trash" / "files"
    if use_trash:
        print("Trash")
        shelf.trash_book(shelf.sections[LIB].cards[LIB / "c.epub"])
        check(wait_for(lambda: not (LIB / "c.epub").exists()), "a book goes away")
        check(wait_for(lambda: (trash / "c.epub").exists()), "it is in the trash, not gone for good")

    print("Reading")
    delta = shelf.sections[studium].cards[studium / "d.epub"]
    wait_for(lambda: delta.info.title == "Delta")
    shelf.open_card(delta)
    reader = window.reader
    check(reader is not None, "the reading page exists")
    check(wait_for(lambda: reader.opened), "a book opens")
    check(wait_for(lambda: len(reader.toc_rows) == 3), "the table of contents is filled")
    check(window.navigation.get_visible_page() is reader, "the reading page is shown")
    check(wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi")), "there is a position in the book")
    window.lookup_action("toggle-toc").activate(None)
    check(reader.split.get_show_sidebar(), "F9 action opens the table of contents")
    first = js(reader, "endpaper.diagnostics()")
    js(reader, "endpaper.next()")
    check(wait_for(lambda: js(reader, "endpaper.diagnostics()")["cfi"] != first["cfi"]), "turning the page moves on")
    spin(0.3)  # a page turn holds the renderer for a moment
    row = list(reader.toc_rows.values())[2]
    reader._on_toc_row(reader.toc_list, row)
    check(wait_for(lambda: "Chapter 3" in js(reader, "endpaper.diagnostics()")["visibleText"]), "the table of contents jumps")
    check(wait_for(lambda: reader.toc_list.get_selected_row() is row), "the current chapter is highlighted")
    check(wait_for(lambda: "Chapter 3" in reader.window_title.get_subtitle()), "the title shows the chapter")

    print("Links")
    js(reader, "endpaper.clickLink(0)")
    check(wait_for(lambda: "Chapter 2" in js(reader, "endpaper.diagnostics()")["visibleText"]), "a link inside the book works")
    check(wait_for(lambda: reader.back_button.get_visible()), "the way back is offered")
    reader.back_button.emit("clicked")
    check(wait_for(lambda: "Chapter 3" in js(reader, "endpaper.diagnostics()")["visibleText"]), "back returns to the old place")

    print("Settings")
    before = js(reader, "endpaper.bookDocument()")
    reader.change_font_size(30)
    check(wait_for(lambda: js(reader, "endpaper.bookDocument()")["fontSize"] != before["fontSize"]), "text size changes")
    check(config.reader_settings()["font_size"] == 130, "text size is remembered")
    reader.change_font_size(None)
    reader.settings["theme"] = "dark"
    reader._apply_settings()
    check(wait_for(lambda: js(reader, "endpaper.bookDocument()")["background"] == "rgb(34, 34, 38)"), "dark colors reach the book")
    reader.settings["theme"] = "sepia"
    reader._apply_settings()
    check(wait_for(lambda: js(reader, "endpaper.bookDocument()")["background"] == "rgb(245, 238, 220)"), "sepia reaches the book")
    reader.settings["theme"] = "light"
    reader._apply_settings()
    window._set_narrow(True)
    check(not reader.next_button.get_visible() and reader.split.get_collapsed(), "a narrow window gives the text the room")
    window._set_narrow(False)
    check(not reader.notes_split.get_show_sidebar(), "a wider window does not open the flyout by itself")

    print("Sidebars")
    reader.toc_button.set_active(True)
    reader.notes_button.set_active(True)
    spin(0.5)
    origin = lambda w: w.compute_point(window, Graphene.Point()) [1].y
    check(abs(origin(reader.toc_list.get_ancestor(Adw.ToolbarView)) - origin(reader.web.get_ancestor(Adw.ToolbarView))) < 1, "the table of contents reaches up as far as the page")
    check(abs(origin(reader.notes_panel.get_ancestor(Adw.ToolbarView)) - origin(reader.web.get_ancestor(Adw.ToolbarView))) < 1, "the notes too")
    reader.notes_button.set_active(False)
    spin(0.3)

    print("Chapters left behind are freed")
    note = """(() => {
        window.leftChapters ??= new Map()
        for (const { doc } of document.querySelector('foliate-view').renderer.getContents())
            if (![...window.leftChapters.values()].some(ref => ref.deref() === doc))
                window.leftChapters.set(window.leftChapters.size, new WeakRef(doc))
    })()"""
    for turn in range(12):
        js(reader, f"endpaper.goToIndex({turn % 2})")
        spin(0.25)
        js(reader, note)
    for _ in range(3):  # enough garbage for the collector to run
        js(reader, "(() => { for (let i = 0; i < 60; i++) new Array(2e6).fill(i) })()")
        spin(0.2)
    kept = js(reader, "(() => { const refs = [...window.leftChapters.values()]; return { seen: refs.length, alive: refs.filter(ref => ref.deref()).length } })()")
    check(kept["seen"] >= 10, "turning chapters opens new documents")
    check(kept["alive"] <= 2, f"only the open chapter stays in memory ({kept['alive']} of {kept['seen']})")

    print("A repeated scroll event")
    repeats = reader._scroll_is_repeat
    check(repeats((100, (0.0, 6.0), True)) is False, "a scroll event gets through the first time")
    check(repeats((100, (0.0, 6.0), True)) is True, "but not again, which would set off a flood of events in WebKit")
    check(repeats((101, (0.0, 6.0), True)) is False, "the next one is a new event")
    check(repeats((100, (0.0, 6.0), True)) is True, "and the first stays recognized after it")

    print("Two pages side by side")
    layout = lambda: js(reader, "endpaper.pageLayout()")
    reader.toc_button.set_active(False)
    spin(0.6)
    check(layout()["viewWidth"] >= 1100, "the test window is wide enough to try this")
    check(wait_for(lambda: layout()["columns"] == 2), "a wide window shows two pages")
    spread = layout()
    js(reader, "endpaper.goToIndex(1)")
    spin(0.6)
    spread = layout()
    check(spread["text"].strip() != "", "both pages show text")
    js(reader, "endpaper.next()")
    wait_for(lambda: layout()["start"] != spread["start"])
    spin(0.4)
    check(layout()["start"] - spread["start"] == spread["size"], "turning the page moves on by both pages")
    here = layout()["text"][:40]
    reader.toc_button.set_active(True)
    check(wait_for(lambda: layout()["columns"] == 1), "with less room there is one page again, without reopening")
    spin(0.4)
    check(here in layout()["text"], "the place in the book is kept")
    reader.toc_button.set_active(False)
    check(wait_for(lambda: layout()["columns"] == 2), "with more room there are two pages again")
    spin(0.4)
    check(here in layout()["text"], "the place is still kept")
    reader.change_font_size(80)
    check(wait_for(lambda: layout()["columns"] == 1), "very large text keeps to one page")
    reader.change_font_size(None)
    check(wait_for(lambda: layout()["columns"] == 2), "normal text goes back to two")
    reader.settings["flow"] = "scrolled"
    reader._apply_settings()
    check(wait_for(lambda: layout()["columns"] == 1), "continuous scrolling stays one column")
    reader.settings["flow"] = "paginated"
    reader._apply_settings()
    check(wait_for(lambda: layout()["columns"] == 2), "and pages come back side by side")

    print("Reading position")
    js(reader, "endpaper.next()")
    spin(0.5)
    position = js(reader, "endpaper.diagnostics()")["cfi"]
    window.navigation.pop()
    check(wait_for(lambda: not reader.opened), "going back closes the book")
    saved = config.State()
    check(saved.position(delta.info.key) == position, "the position is on disk")
    shelf.open_card(delta)
    wait_for(lambda: reader.opened)
    check(wait_for(lambda: js(reader, "endpaper.diagnostics()")["cfi"] == position), "the book opens at the old place")
    window.navigation.pop()
    wait_for(lambda: not reader.opened)

    print("Bad positions and files")
    state.books[delta.info.key]["cfi"] = "epubcfi(/6/999!/4/2)"
    shelf.open_card(delta)
    check(wait_for(lambda: reader.opened), "a position that no longer fits does not stop the book")
    check(wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("visibleText")), "it shows text anyway")
    window.navigation.pop()
    wait_for(lambda: not reader.opened)
    broken = shelf.sections[romane].cards[romane / "kaputt.epub"]
    shelf.open_card(broken)
    spin(0.3)
    check(window.navigation.get_visible_page() is shelf, "an unreadable file stays on the shelf with a message")

    print("Colors of a book that insists on its own")
    make_styled_epub(LIB / "styled.epub")
    check(wait_for(lambda: (LIB / "styled.epub") in shelf.sections[LIB].cards), "the styled book is on the shelf")
    styled = shelf.sections[LIB].cards[LIB / "styled.epub"]
    wait_for(lambda: styled.info.title == "Styled")
    reader.settings["theme"] = "light"
    shelf.open_card(styled)
    check(wait_for(lambda: reader.opened), "it opens")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    colors = lambda name: js(reader, f"endpaper.colorsOf({json.dumps(name)})")
    check(colors("head")["color"] == "rgb(47, 95, 99)", "light colors keep the book's own design")
    anchor = js(reader, 'endpaper.selectionAnchor("inbox")')
    for theme in ("dark", "sepia", "sage", "black"):
        reader.settings["theme"] = theme
        reader._apply_settings()
        spin(0.4)
        audit = js(reader, "endpaper.contrastAudit()")
        check(audit["checked"] >= 5 and audit["below"] == 0, f"{theme}: all text is readable, also against !important ({audit['lowest']}:1)")
    reader.settings["theme"] = "dark"
    reader._apply_settings()
    spin(0.4)
    head = [int(v) for v in colors("head")["color"][4:-1].split(",")]
    check(sum(head) > 300 and max(head) - min(head) > 15, "a colored heading is lifted, and keeps its hue")
    check(colors("gray")["color"] == "rgb(227, 227, 230)", "gray text takes the theme's text color")
    check(colors("box")["background"] == "rgb(50, 50, 56)", "a light box stays a box")
    check(colors("banner")["background"] == "rgb(20, 48, 102)" and colors("banner")["color"] == "rgb(255, 255, 255)", "a dark banner with white text stays as it is")
    check(colors("rule")["border"] != "rgb(20, 48, 102)", "a dark rule becomes visible")
    check(colors("pic")["inline"] is None or "filter" not in colors("pic")["inline"], "pictures are left alone")
    check(colors("picbox")["background"] == "rgb(205, 219, 220)", "a box with a picture behind it keeps its light color")
    on_picture = [int(v) for v in colors("onpic")["color"][4:-1].split(",")]
    field = [int(v) for v in colors("picfield")["color"][4:-1].split(",")]
    check(sum(on_picture) < 200 and sum(field) < 200, f"and its text and fields stay dark ({colors('onpic')['color']}, {colors('picfield')['color']})")
    check(js(reader, 'endpaper.selectionAnchor("inbox")') == anchor, "anchors for notes do not change")
    reader.settings["theme"] = "light"
    reader._apply_settings()
    spin(0.4)
    check(colors("head")["color"] == "rgb(47, 95, 99)" and not colors("head")["inline"], "going back to light undoes everything")
    reader.settings["theme"] = "dark"
    reader._apply_settings()
    spin(0.3)
    js(reader, "endpaper.goToIndex(1)")
    spin(0.5)
    js(reader, "endpaper.goToIndex(0)")
    spin(0.5)
    check(colors("head")["color"] != "rgb(47, 95, 99)", "a section that loads again is adapted again")
    reader.settings["theme"] = "light"
    window.navigation.pop()
    wait_for(lambda: not reader.opened)

    print("Marking with two clicks")
    make_epub(LIB / "marks.epub", title="Marks", identifier="urn:marks", paragraphs=30)
    book_bytes = (LIB / "marks.epub").read_bytes()
    check(wait_for(lambda: (LIB / "marks.epub") in shelf.sections[LIB].cards), "the book is on the shelf")
    marked = shelf.sections[LIB].cards[LIB / "marks.epub"]
    wait_for(lambda: marked.info.title == "Marks")
    shelf.open_card(marked)
    check(wait_for(lambda: reader.opened), "it opens")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    spin(0.3)
    notes_file = LIB / "marks.notes.md"
    state = lambda: js(reader, "endpaper.markState()")
    click = lambda selector, word: js(reader, f"endpaper.clickWord({json.dumps(selector)}, {json.dumps(word)})")
    quotes = lambda: [e.quote for e in reader.notes.entries]
    messages = []
    reader.show_message = messages.append
    check(reader.mark_buttons["green"].get_mapped(), "the bar is on screen")
    click("p:nth-of-type(2)", "chapter")
    spin(0.2)
    check(state()["pending"] is False and not reader.notes.entries, "without marking mode, clicks mark nothing")
    check(not notes_file.exists(), "no notes file without a mark")
    reader.mark_buttons["green"].set_active(True)
    check(wait_for(lambda: state()["marking"]), "a color starts marking")
    check(reader.mark_done.get_visible() and reader.mark_hint.get_visible(), "the bar shows how it works and how to end")
    click("p:nth-of-type(2)", "Sentence")
    check(wait_for(lambda: state()["pending"]), "the first click remembers the word")
    check(wait_for(lambda: "last word" in reader.mark_hint.get_label()), "the bar says what comes next")
    check(not notes_file.exists(), "one click alone marks nothing yet")
    click("p:nth-of-type(2)", "while")
    check(wait_for(lambda: len(quotes()) == 1), "the second click marks")
    entry = reader.notes.entries[0]
    check(entry.quote.startswith("1 Sentence") is False and entry.quote.startswith("Sentence") and entry.quote.endswith("while."), "from the first word to the last, with its period")
    check(entry.color == "green" and entry.style == "highlight" and entry.chapter == "Chapter 1", "with color and chapter")
    check(entry.quote in notes_file.read_text(encoding="utf-8").replace("\n> ", " "), "it is in the notes file at once")
    check(wait_for(lambda: state()["drawn"] == 1), "it is drawn in the text")
    check(state()["marking"], "marking goes on after a mark")
    click("p:nth-of-type(3)", "chapter")
    wait_for(lambda: state()["pending"])
    click("p:nth-of-type(3)", "chapter")
    check(wait_for(lambda: len(quotes()) == 2), "a second click on the same word marks it")
    check(reader.notes.entries[1].quote == "chapter", "only that word")
    click("p:nth-of-type(4)", "while")
    wait_for(lambda: state()["pending"])
    click("p:nth-of-type(4)", "Sentence")
    check(wait_for(lambda: len(quotes()) == 3), "the other way round works too")
    check(reader.notes.entries[2].quote.startswith("Sentence") and reader.notes.entries[2].quote.endswith("while."), "and gives the same range")
    click("p:nth-of-type(5)", "Sentence")
    wait_for(lambda: state()["pending"])
    reader.on_escape()
    check(wait_for(lambda: not state()["pending"]), "Esc forgets the first word")
    check(state()["marking"], "and stays in marking mode")
    js(reader, "endpaper.clickAt(3, 3)")
    spin(0.2)
    check(state()["pending"] is False, "a click in the margin is no word")
    click("#to-two", "chapter")
    spin(0.5)
    check("Chapter 2" not in js(reader, "endpaper.diagnostics()")["visibleText"] and state()["pending"], "in marking mode a link is a word, not a link")
    reader.on_escape()
    wait_for(lambda: not state()["pending"])
    reader.mark_buttons["blue"].set_active(True)
    click("p:nth-of-type(5)", "Sentence")
    wait_for(lambda: state()["pending"])
    position = js(reader, "endpaper.pageLayout()")["start"]
    js(reader, "endpaper.next()")
    wait_for(lambda: js(reader, "endpaper.pageLayout()")["start"] != position)
    spin(0.4)
    click("p:nth-of-type(28)", "while")
    check(wait_for(lambda: len(quotes()) == 4), "a mark can go across pages")
    check(reader.notes.entries[3].color == "blue" and len(reader.notes.entries[3].quote) > 2000, "with the other color, over all the text between")
    click("p:nth-of-type(28)", "Sentence")
    wait_for(lambda: state()["pending"])
    js(reader, "endpaper.goToIndex(1)")
    spin(0.6)
    click("p:nth-of-type(3)", "Sentence")
    check(wait_for(lambda: messages), "across chapters there is a clear message")
    check(state()["pending"] and len(quotes()) == 4, "and the new word becomes the start")
    reader.on_escape()
    wait_for(lambda: not state()["pending"])
    reader.underline_button.set_active(True)
    click("p:nth-of-type(3)", "chapter")
    wait_for(lambda: state()["pending"])
    click("p:nth-of-type(3)", "chapter")
    check(wait_for(lambda: any(e.style == "underline" for e in reader.notes.entries)), "underlining works")
    window.lookup_action("toggle-marking").activate(None)
    check(wait_for(lambda: not state()["marking"]) and not reader.mark_done.get_visible(), "the shortcut ends marking")
    window.lookup_action("toggle-marking").activate(None)
    check(wait_for(lambda: state()["marking"]), "and starts it again")
    reader.mark_done.emit("clicked")
    check(wait_for(lambda: not state()["marking"]) and not reader.marking, "Done ends marking")
    check(not reader.underline_button.get_active(), "and underlining is off again")
    before = len(quotes())
    click("p:nth-of-type(6)", "Sentence")
    click("p:nth-of-type(6)", "while")
    spin(0.3)
    check(len(quotes()) == before, "afterwards clicks mark nothing")

    print("Marking with the keyboard")
    from gi.repository import Gdk
    js(reader, "endpaper.goToIndex(2)")
    spin(0.6)
    reader.web.grab_focus()
    press = lambda key, mods=Gdk.ModifierType(0): reader._on_key(None, key, 0, mods)
    check(press(Gdk.KEY_Right) is False, "outside marking mode the keys stay with the book")
    window.lookup_action("toggle-marking").activate(None)
    wait_for(lambda: state()["marking"])
    reader.web.grab_focus()
    cursor = lambda: state()["cursor"]
    check(cursor() is None, "at first no cursor gets in the way of the mouse")
    press(Gdk.KEY_Right)
    check(wait_for(lambda: cursor()), "the first key shows the cursor")
    check(cursor()["text"] == "Chapter" and cursor()["onScreen"], "on the first word on the page")
    press(Gdk.KEY_Right)
    check(wait_for(lambda: cursor()["text"] == "3"), "the right arrow goes to the next word")
    press(Gdk.KEY_Left)
    check(wait_for(lambda: cursor()["text"] == "Chapter"), "the left arrow goes back")
    press(Gdk.KEY_Down)
    wait_for(lambda: cursor()["text"] != "Chapter")
    below = cursor()["text"]
    press(Gdk.KEY_Up)
    check(below != "Chapter" and wait_for(lambda: cursor()["text"] == "Chapter"), "up and down go from line to line")
    for _times in range(4):
        press(Gdk.KEY_Down)
    wait_for(lambda: cursor()["text"] not in ("Chapter", below))
    spin(0.3)
    before = len(quotes())
    press(Gdk.KEY_Return)
    check(wait_for(lambda: state()["pending"]), "Enter chooses the first word")
    start_word = cursor()["text"]
    for _times in range(3):
        press(Gdk.KEY_Right)
    spin(0.3)
    end_word = cursor()["text"]
    check(state()["drawn"] >= 3, "the range shows while moving")
    press(Gdk.KEY_space)
    check(wait_for(lambda: len(quotes()) == before + 1), "space chooses the last word and marks")
    made = next(e for e in reader.notes.entries if e.quote.split()[0].startswith(start_word) and e.quote.split()[-1].startswith(end_word) and len(e.quote.split()) == 4)
    check(made.chapter == "Chapter 3", "four words, as chosen")
    press(Gdk.KEY_Down)
    spin(0.2)
    press(Gdk.KEY_Return)
    wait_for(lambda: state()["pending"])
    press(Gdk.KEY_Return)
    check(wait_for(lambda: len(quotes()) == before + 2), "Enter twice marks one word")
    check(len(reader.notes.entries[-1].quote.split()) == 1 or any(len(e.quote.split()) == 1 and e.chapter == "Chapter 3" for e in reader.notes.entries), "only that word")
    press(Gdk.KEY_Right)
    spin(0.1)
    press(Gdk.KEY_Return)
    wait_for(lambda: state()["pending"])
    press(Gdk.KEY_Escape)
    check(wait_for(lambda: not state()["pending"]) and state()["marking"], "Esc forgets the word and stays in marking mode")
    press(Gdk.KEY_3)
    check(reader.mark_buttons["blue"].get_active(), "a number chooses a color")
    press(Gdk.KEY_u)
    check(reader.underline_button.get_active(), "u underlines")
    press(Gdk.KEY_u)
    check(not reader.underline_button.get_active(), "u again does not")
    press(Gdk.KEY_Up)
    spin(0.2)
    press(Gdk.KEY_F10, Gdk.ModifierType.SHIFT_MASK)
    wait_for(lambda: messages[-1:] and "no mark" in messages[-1] or reader.mark_popover.get_visible(), 3)
    for _times in range(40):
        if reader.mark_popover.get_visible():
            break
        press(Gdk.KEY_Left)
        spin(0.08)
        press(Gdk.KEY_F10, Gdk.ModifierType.SHIFT_MASK)
        spin(0.12)
    check(reader.mark_popover.get_visible(), "the menu key opens the options of a mark")
    check(press(Gdk.KEY_Escape) is False and state()["marking"], "Esc in the menu stays in the menu, not the mode")
    reader.mark_popover.popdown()
    spin(0.2)
    reader.web.grab_focus()
    page = js(reader, "endpaper.pageLayout()")["start"]
    press(Gdk.KEY_Page_Down)
    wait_for(lambda: js(reader, "endpaper.pageLayout()")["start"] != page)
    check(wait_for(lambda: cursor() and cursor()["onScreen"]), "turning the page brings the cursor along")
    page = js(reader, "endpaper.pageLayout()")["start"]
    for _times in range(1500):
        press(Gdk.KEY_Right)
        if _times % 25 == 24:
            spin(0.05)
            if js(reader, "endpaper.pageLayout()")["start"] != page:
                break
    spin(0.3)
    check(js(reader, "endpaper.pageLayout()")["start"] != page and cursor()["onScreen"], "a cursor that leaves the page turns it")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: reader.notes_split.get_show_sidebar())
    spin(0.2)
    focus = window.get_focus()
    check(focus is not None and focus.is_ancestor(reader.notes_panel), "the shortcut for notes brings the keyboard there")
    check(press(Gdk.KEY_Right) is False, "there the keys are not for marking")
    check(press(Gdk.KEY_Escape) is True, "Esc closes the flyout")
    check(wait_for(lambda: not reader.notes_split.get_show_sidebar()) and state()["marking"], "and marking goes on")
    window.lookup_action("toggle-marking").activate(None)
    check(wait_for(lambda: not state()["marking"]) and cursor() is None, "ending marking takes the cursor away")

    print("Changing a mark")
    js(reader, "endpaper.goToIndex(0)")
    spin(0.6)
    check(wait_for(lambda: state()["drawn"] >= 4), "marks of a section come back when it loads again")
    click("p:nth-of-type(3)", "chapter")
    check(wait_for(lambda: reader.mark_popover.get_visible()), "a click on a mark opens its options")
    single = next(e for e in reader.notes.entries if e.quote == "chapter" and e.chapter == "Chapter 1")
    reader.popover_colors["pink"].set_active(True)
    check(next(e for e in reader.notes.entries if e.id == single.id).color == "pink", "another color is saved at once")
    check(not hasattr(reader, "popover_note"), "the menu only changes the mark, the writing happens in the flyout")
    reader.popover_add_note.emit("clicked")
    check(wait_for(lambda: reader.notes_split.get_show_sidebar()), "adding a note opens the flyout")
    panel = reader.notes_panel
    row = lambda: next(r for r in panel.rows if r.entry_id == single.id)
    check(wait_for(lambda: any(r.entry_id == single.id for r in panel.rows)), "with a field for the note")
    check(wait_for(lambda: window.get_focus() is row().view), "and the cursor in it")
    check(not reader.mark_popover.get_visible(), "the menu is gone")
    saved = lambda: next(e for e in NotesFile(LIB / "marks.epub").entries if e.id == single.id)
    check(saved().notes == [], "an empty field is no note")
    long_text = "My **bold** note\n\n# A heading\n\n- a list\n\n" + "\n\n".join(f"Paragraph {n}" for n in range(60))
    row().view.get_buffer().set_text(long_text)
    check(wait_for(lambda: saved().notes and saved().notes[0].text == long_text), "typing is saved by itself, soon after a pause")
    raw = notes_file.read_text(encoding="utf-8")
    check("My **bold** note\n\n# A heading\n\n- a list" in raw and "\\#" not in raw, "as plain Markdown in the file")
    check(wait_for(lambda: len(state()["signs"]) == 1), "a mark with a note gets a sign")
    view = row().view
    buffer = view.get_buffer()
    bold_at = buffer.get_iter_at_offset(long_text.index("bold"))
    check(bold_at.has_tag(view.tags["bold"]), "Markdown shows while typing: bold")
    heading_at = buffer.get_iter_at_offset(long_text.index("A heading"))
    check(heading_at.has_tag(view.tags["h1"]), "and a heading")
    check(buffer.get_iter_at_offset(long_text.index("**")).has_tag(view.tags["syntax"]), "with the marks dimmed")
    check(wait_for(lambda: view.get_height() > 600), "the field grows with the text")
    wrapped = "A long note that has to wrap over several lines in the narrow flyout, " * 6
    reader.notes.add_note(single.id, wrapped.strip())
    reader._notes_changed()
    wrapping = next(r for r in panel.rows if r.view.text == wrapped.strip())
    def fits():
        end = wrapping.view.get_iter_location(wrapping.view.get_buffer().get_end_iter())
        return end.y > 3 * end.height and end.y + end.height <= wrapping.view.get_height()
    check(wait_for(fits, 5), "wrapped lines are not cut off")
    reader.notes_button.set_active(False)
    spin(0.4)
    reader.notes_button.set_active(True)
    check(wait_for(fits, 5), "also when the flyout opens again")
    remove_note(wrapping)
    spin(0.2)
    row().view.grab_focus()
    panel.add_empty_row(single.id)
    second = next(r for r in panel.rows if r.entry_id == single.id and r.note_id is None)
    check(wait_for(lambda: window.get_focus() is second.view), "another note can be added")
    second.view.get_buffer().set_text("A second note")
    check(wait_for(lambda: [n.text for n in saved().notes] == [long_text, "A second note"]), "a mark can have many notes")
    second.view.get_buffer().set_text("Changed quickly")
    panel.flush()
    check([n.text for n in saved().notes] == [long_text, "Changed quickly"], "flushing saves at once")
    first_note = saved().notes[0].id
    second.view.get_buffer().set_text("")
    check(wait_for(lambda: [n.id for n in saved().notes] == [first_note]), "an emptied field takes its note away")
    second.view.get_buffer().set_text("Back again")
    check(wait_for(lambda: [n.text for n in saved().notes] == [long_text, "Back again"]), "and typing brings it back")
    remove_note(second)
    check(wait_for(lambda: [n.id for n in saved().notes] == [first_note]), "the bin removes one note")
    check(len(state()["signs"]) == 1, "the sign stays for the other")
    window.lookup_action("toggle-notes").activate(None)
    check(wait_for(lambda: not reader.notes_split.get_show_sidebar()), "the shortcut closes the flyout")
    spin(0.5)
    sign = state()["signs"][0]
    word = js(reader, """(() => {
        const { doc } = document.querySelector('foliate-view').renderer.getContents()[0]
        const p = doc.querySelector('p:nth-of-type(3)')
        const range = doc.createRange()
        range.selectNodeContents(p)
        const line = range.getClientRects()[0]
        return { left: line.left, top: line.top, bottom: line.bottom }
    })()""")
    check(sign["x"] + sign["size"] + 10 <= word["left"], "the sign is beside the text, with some room")
    check(sign["size"] >= 0.6 * (word["bottom"] - word["top"]) and sign["size"] <= word["bottom"] - word["top"], "as big as fits the line")
    check(sign["y"] >= word["top"] and sign["y"] + sign["size"] <= word["bottom"], "and level with it")
    reader.change_font_size(40)
    check(wait_for(lambda: state()["signs"] and state()["signs"][0]["size"] > sign["size"]), "larger text gets a larger sign")
    check(state()["signs"][0]["size"] <= 18, "but it stays small")
    reader.change_font_size(None)
    check(wait_for(lambda: state()["signs"] and state()["signs"][0]["size"] == sign["size"]), "and back")
    spin(0.3)
    sign = state()["signs"][0]
    click("p:nth-of-type(3)", "Sentence")  # the same paragraph, not the mark: no options
    spin(0.3)
    check(not reader.mark_popover.get_visible(), "clicks beside a mark do not open it")
    js(reader, f"endpaper.clickAt({sign['x'] + 6}, {sign['y'] + 6})")
    check(wait_for(lambda: reader.notes_split.get_show_sidebar()), "a click on the sign opens the flyout")
    check(wait_for(lambda: reader.notes_panel.cards[single.id].get_first_child().has_focus()), "at the mark")
    check(not row().view.editing, "to read, not yet to write")
    check(not reader.mark_popover.get_visible(), "without the menu")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())
    js(reader, "endpaper.goToIndex(1)")
    check(wait_for(lambda: state()["signs"] == []), "another chapter shows no sign of this one")
    js(reader, "endpaper.goToIndex(0)")
    check(wait_for(lambda: len(state()["signs"]) == 1), "also after the section loads again")
    spin(0.3)
    window.lookup_action("toggle-notes").activate(None)
    check(wait_for(lambda: panel.cards.get(single.id) is not None), "the flyout lists the marks of the book")
    check(len(panel.cards) == len(reader.notes.entries), "all of them")
    js(reader, "endpaper.goToIndex(1)")
    spin(0.5)
    panel.on_jump(single.cfi)
    check(wait_for(lambda: "Chapter 1" in js(reader, "endpaper.diagnostics()")["visibleText"] or js(reader, "endpaper.markState()")["signs"]), "a click on a quote goes to the place")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())
    spin(0.4)
    click("p:nth-of-type(3)", "chapter")
    check(wait_for(lambda: reader.mark_popover.get_visible()), "the mark can still be opened")
    drawn = state()["drawn"]
    reader.popover_remove.emit("clicked")
    dialog = reader.remove_dialog
    check(dialog is not None, "a mark with a note asks first")
    dialog.emit("response", "remove")
    dialog.force_close()
    check(wait_for(lambda: state()["drawn"] == drawn - 1), "removing takes it out of the text")
    check(all(e.id != single.id for e in NotesFile(LIB / "marks.epub").entries), "and out of the file")
    check(wait_for(lambda: state()["signs"] == []), "and its sign with it")

    print("Undo")
    undo_key = lambda: reader._on_key(None, Gdk.KEY_z, 0, Gdk.ModifierType.CONTROL_MASK)
    redo_key = lambda: reader._on_key(None, Gdk.KEY_Z, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
    reader.web.grab_focus()
    spin(0.2)
    count = len(reader.notes.entries)
    known = {e.id for e in reader.notes.entries}
    drawn = state()["drawn"]
    reader.mark_buttons["yellow"].set_active(True)
    click("p:nth-of-type(7)", "Sentence")
    wait_for(lambda: state()["pending"])
    click("p:nth-of-type(7)", "while")
    check(wait_for(lambda: len(reader.notes.entries) == count + 1) and wait_for(lambda: state()["drawn"] == drawn + 1), "a new mark")
    new_mark = next(e for e in reader.notes.entries if e.id not in known)
    reader.mark_done.emit("clicked")
    wait_for(lambda: not state()["marking"])
    check(undo_key() is True and len(reader.notes.entries) == count, "Ctrl+Z takes it back")
    check(wait_for(lambda: state()["drawn"] == drawn), "out of the text as well")
    check(all(e.id != new_mark.id for e in NotesFile(LIB / "marks.epub").entries), "and out of the file")
    check(redo_key() is True and any(e.id == new_mark.id for e in reader.notes.entries), "Ctrl+Shift+Z brings it back")
    check(wait_for(lambda: state()["drawn"] == drawn + 1), "into the text as well")
    click("p:nth-of-type(7)", "Sentence")
    check(wait_for(lambda: reader.mark_popover.get_visible()), "the menu of the mark")
    check(undo_key() is False, "while the menu is open, the keys stay there")
    reader.popover_remove.emit("clicked")
    check(wait_for(lambda: state()["drawn"] == drawn), "removing the mark")
    spin(0.2)
    undo_key()
    check(any(e.id == new_mark.id for e in NotesFile(LIB / "marks.epub").entries) and wait_for(lambda: state()["drawn"] == drawn + 1), "a removed mark comes back")
    undo_key()
    undo_key()
    check(all(e.id != new_mark.id for e in reader.notes.entries), "and further back it is gone")
    messages.clear()
    for _times in range(300):
        undo_key()
        if messages and "nothing" in messages[-1]:
            break
        messages.clear()
    check(messages and "nothing" in messages[-1], "at the very start there is a clear message")
    for _times in range(300):
        messages.clear()
        redo_key()
        if messages and "nothing" in messages[-1]:
            break
    check(len(reader.notes.entries) >= count, "everything can be restored")
    def refuse():
        raise NotesKept("a mark")
    reader.notes._undo.append((refuse, refuse))
    messages.clear()
    check(undo_key() is True and messages and "nothing you wrote" in messages[-1], "what would lose writing is left out, with a message")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: reader.notes_panel.cards)
    reader.notes_panel.add_empty_row(next(iter(reader.notes_panel.cards)))
    field = next(r for r in reader.notes_panel.rows if r.note_id is None)
    wait_for(lambda: window.get_focus() is field.view)
    check(undo_key() is False, "in a note field, Ctrl+Z belongs to the field")
    field.view.get_buffer().set_text("A note to throw away")
    field.save()
    owner = next(e for e in reader.notes.entries if e.id == field.entry_id)
    check(any(n.text == "A note to throw away" for n in owner.notes), "a note")
    remove_note(field)
    owner = next(e for e in reader.notes.entries if e.id == field.entry_id)
    check(not any(n.text == "A note to throw away" for n in owner.notes), "thrown away with the bin")
    reader.web.grab_focus()
    undo_key()
    owner = next(e for e in NotesFile(LIB / "marks.epub").entries if e.id == owner.id)
    check(any(n.text == "A note to throw away" for n in owner.notes), "can come back")
    check(wait_for(lambda: any(r.view.text == "A note to throw away" for r in reader.notes_panel.rows)), "also in the flyout")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())

    print("Notes read as finished text")
    panel = reader.notes_panel
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: panel.cards)
    reader.web.grab_focus()
    first_card = next(iter(panel.cards))
    panel.add_empty_row(first_card)
    field = next(r for r in panel.rows if r.note_id is None)
    written = "# Title\n\nSome **bold** and a [link](https://example.org)\n\n- [ ] A task and[^1]\n\n---\n\n```\ncode\n```"
    wait_for(lambda: field.view.has_focus())
    field.view.get_buffer().set_text(written)
    check(wait_for(lambda: field.view.editing), "while writing, the marks are there")
    hidden = lambda text: field.view.get_buffer().get_iter_at_offset(written.index(text)).has_tag(field.view.tags["hidden"])
    check(not hidden("**"), "visibly")
    reader.web.grab_focus()
    check(wait_for(lambda: not field.view.editing), "without the cursor, it is for reading")
    check(hidden("**") and hidden("# ") and hidden("](https") and hidden("```"), "and the marks are gone from view")
    check(not hidden("bold") and not hidden("[ ]") and not hidden("[^1]") and not hidden("---"), "while text, task boxes, footnotes and rules stay")
    check(field.view.text == written, "the field still holds all of it")
    saved_note = next(e for e in NotesFile(LIB / "marks.epub").entries if e.id == first_card).note(field.note_id)
    check(saved_note is not None and saved_note.text == written, "and so does the file")
    field.view.grab_focus()
    check(wait_for(lambda: field.view.editing and not hidden("**")), "clicking in brings the marks back")
    reader.web.grab_focus()
    remove_note(field)
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())

    print("A wider flyout")
    reader.notes_button.set_active(True)
    spin(0.5)
    sidebar = reader.notes_split.get_sidebar()
    normal = sidebar.get_width()
    reader.notes_wide_button.set_active(True)
    check(wait_for(lambda: sidebar.get_width() == normal * 2, 5), f"one click doubles the width ({normal} px)")
    check(config.reader_settings()["notes_wide"] is True, "and is remembered")
    reader.notes_wide_button.set_active(False)
    check(wait_for(lambda: sidebar.get_width() == normal, 5), "another click brings it back")
    check(config.reader_settings()["notes_wide"] is False, "also when remembered")
    reader.notes_button.set_active(False)
    spin(0.3)

    print("Previews of footnotes and references")
    was_book = next(c for c in shelf.sections[LIB].cards.items() if c[0].name == "marks.epub")[1]
    reader.on_opened = None
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    make_notes_epub(LIB / "notes.epub")
    check(wait_for(lambda: (LIB / "notes.epub") in shelf.sections[LIB].cards), "a book with footnotes")
    notes_book = shelf.sections[LIB].cards[LIB / "notes.epub"]
    wait_for(lambda: notes_book.info is not None)
    shelf.open_card(notes_book)
    check(wait_for(lambda: reader.opened), "opens")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    peek = lambda: js(reader, "endpaper.peekState()")
    link = lambda text, kind: js(reader, f"endpaper.linkEvent({json.dumps(text)}, {json.dumps(kind)})")
    def hovered(text):
        js(reader, "endpaper.peekHide()")
        link(text, "mouseover")
        wait_for(lambda: peek()["visible"], 3)
        return peek()
    check(js(reader, 'endpaper.isShown("note1")') is False and js(reader, 'endpaper.isShown("plain2")') is False, "footnotes are not in the running text")
    check(js(reader, 'endpaper.isShown("target-para")') is True, "the text is")
    shown = hovered("note-mark")
    check(shown["visible"] and "text of the first footnote" in shown["text"], "hovering shows the footnote")
    check("strong" in shown["tags"] and shown["inWindow"], "with its emphasis, within the window")
    mark_place = link("note-mark", "rect")
    check(shown["side"] in ("left", "right"), "beside the link")
    check(shown["box"]["left"] >= mark_place["right"] or shown["box"]["right"] <= mark_place["left"], "leaving it free")
    link("note-mark", "mouseout")
    check(wait_for(lambda: not peek()["visible"], 3), "and it goes when the pointer leaves")
    check("plain footnote" in hovered("[2]")["text"], "a footnote without markup too")
    shown = hovered("bad-mark")
    check("Harmless words" in shown["text"] and "more words" in shown["text"], "a note's text is shown")
    check(all(tag not in ("img", "script", "a") for tag in shown["tags"]), "but none of its pictures, scripts or links")
    spin(0.3)
    check(js(reader, "endpaper.diagnostics()")["pwned"] is None, "nothing of it runs")
    check("worth" in hovered("same-chapter")["text"], "a reference in the same chapter")
    shown = hovered("verse-link")
    check("Second verse" in shown["text"] and "goes on." in shown["text"], "a reference to another chapter")
    check("First verse" not in shown["text"] and "Third verse" not in shown["text"], "with just the verse it points to")
    check("*" not in shown["text"], "and without the marks of its own footnotes")
    shown = hovered("long-link")
    check(len(shown["text"]) < 1600 and shown["text"].endswith("…"), "a long text is cut")
    check(shown["box"]["bottom"] - shown["box"]["top"] > 300 and shown["inWindow"], "and a lot of it takes the height it needs, within the window")
    shown = hovered("heading-link")
    check("Two" in shown["text"] and "after the heading" in shown["text"], "a heading brings its paragraph")
    js(reader, "endpaper.peekHide()")
    link("chapter-link", "mouseover")
    spin(0.6)
    check(not peek()["visible"], "a link to a whole chapter has no preview")
    link("nothing-mark", "mouseover")
    spin(0.6)
    check(not peek()["visible"], "nor one that leads nowhere")
    link("web-link", "mouseover")
    spin(0.6)
    check(not peek()["visible"], "nor a web link: nothing leaves the book")
    shown = hovered("excerpt-link")
    check("Body of the first excerpt" in shown["text"] and "Heading of the first excerpt" in shown["text"], "an excerpt shows in full")
    check(shown["text"].startswith("First Source p. 3"), "its title without the marks around it")
    check("*" not in shown["text"], "and none of them anywhere")
    check("A footnote inside" not in shown["text"], "without the footnotes in it")
    check("Second Source" not in shown["text"] and "second excerpt" not in shown["text"], "and only this excerpt")
    js(reader, "endpaper.peekHide()")
    place_now = js(reader, "endpaper.diagnostics()")["visibleText"]
    link("excerpt-link", "click")
    check(wait_for(lambda: peek()["visible"] and peek()["pinned"], 3), "clicking an excerpt keeps it open")
    check(js(reader, "endpaper.diagnostics()")["visibleText"] == place_now, "and stays on the page")
    check(peek()["go"], "with the way to its place, if wanted")
    js(reader, "endpaper.peekGo()")
    check(wait_for(lambda: "Heading of the first excerpt" in js(reader, "endpaper.diagnostics()")["visibleText"] or "Excerpts" in js(reader, "endpaper.diagnostics()")["visibleText"] or "First Source" in js(reader, "endpaper.diagnostics()")["visibleText"], 5), "which works")
    reader.back_button.emit("clicked")
    wait_for(lambda: "Main text" in js(reader, "endpaper.diagnostics()")["visibleText"], 5)
    link("note-mark", "click")
    check(wait_for(lambda: peek()["visible"] and peek()["pinned"], 3), "clicking a footnote keeps it open")
    check("Chapter" not in js(reader, "endpaper.diagnostics()")["visibleText"] and "Main text" in js(reader, "endpaper.diagnostics()")["visibleText"], "and stays on the page")
    check(not peek()["go"], "with no way to a place that is not in the text")
    link("note-mark", "mouseout")
    spin(0.6)
    check(peek()["visible"], "it does not go with the pointer")
    js(reader, "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))")
    check(not peek()["visible"], "Esc closes it")
    link("note-mark", "focusin")
    check(wait_for(lambda: peek()["visible"], 3), "a link reached with the keyboard shows it too")
    js(reader, "endpaper.peekHide()")
    link("verse-link", "click")
    check(wait_for(lambda: "Two" in js(reader, "endpaper.diagnostics()")["visibleText"], 5), "clicking a reference goes there")
    check(not peek()["visible"], "without a popup in the way")
    reader.back_button.emit("clicked")
    check(wait_for(lambda: "Main text" in js(reader, "endpaper.diagnostics()")["visibleText"], 5), "and back")
    js(reader, "document.querySelector('foliate-view').renderer.getContents()[0].doc.querySelector('a[href]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch' }))")
    link("same-chapter", "click")
    check(wait_for(lambda: peek()["pinned"] and peek()["go"], 3), "tapping a reference shows it, with a way there")
    js(reader, "endpaper.peekGo()")
    check(wait_for(lambda: not peek()["visible"], 3), "that way works")
    js(reader, "document.querySelector('foliate-view').renderer.getContents()[0].doc.querySelector('a[href]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse' }))")
    js(reader, "endpaper.goToIndex(0)")
    wait_for(lambda: "Main text" in js(reader, "endpaper.diagnostics()")["visibleText"], 5)
    reader.mark_buttons["green"].set_active(True)
    spin(0.2)
    link("note-mark", "mouseover")
    spin(0.6)
    check(not peek()["visible"], "while marking, nothing pops up")
    reader.stop_marking()
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    shelf.open_card(was_book)
    check(wait_for(lambda: reader.opened), "back to the other book")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))

    print("Answer fields")
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    study_path = make_answers_epub(LIB / "study.epub")
    before_bytes = study_path.read_bytes()
    check(wait_for(lambda: study_path in shelf.sections[LIB].cards), "a study edition")
    study = shelf.sections[LIB].cards[study_path]
    wait_for(lambda: study.info is not None)
    def open_study():
        shelf.open_card(study)
        check(wait_for(lambda: reader.opened), "opens")
        wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    def field(selector):
        return js(reader, f"endpaper.fieldState({json.dumps(selector)})")
    def type_in(selector, value):
        js(reader, f"endpaper.fieldInput({json.dumps(selector)}, {json.dumps(value)})")
    def study_notes():
        return NotesFile(study_path, "Study Book")
    def saved_answer(key):
        return next((a for a in study_notes().answers if a.field.endswith(key)), None)
    open_study()
    check(field("#a1")["shown"] and field("#c1")["shown"] and field("#t1")["shown"], "its text fields, tick boxes and lines are there")
    check(not field("#r1")["shown"] and not field("#s1")["shown"] and not field("#b1")["shown"], "other controls, which cannot be saved, are not")
    place_before = js(reader, "endpaper.diagnostics()")["cfi"]
    typed_keys = (" ", "ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "Backspace")
    for selector in ("#a1", "#t1"):
        taken = [key for key in typed_keys if js(reader, f"endpaper.keyIn({json.dumps(selector)}, {json.dumps(key)})")]
        check(taken == [], f"typing in {selector}, no key turns the page ({taken})")
    spin(0.3)
    check(js(reader, "endpaper.diagnostics()")["cfi"] == place_before, "and the page stays")
    check(js(reader, 'endpaper.keyIn(null, "ArrowRight")') is True, "outside of a field, keys still turn the page")
    wait_for(lambda: js(reader, "endpaper.diagnostics()")["cfi"] != place_before, 3)
    js(reader, "endpaper.prev()")
    wait_for(lambda: js(reader, "endpaper.diagnostics()")["cfi"] == place_before, 3)
    type_in("#a1", "My first answer")
    check(wait_for(lambda: saved_answer("#field-1") is not None, 3), "an answer is saved while typing")
    check(saved_answer("#field-1").question == "What is the first question?", "with its question")
    type_in("#a2", "Twin one")
    type_in("#a3", "Twin two")
    type_in("#t1", "A line")
    js(reader, "endpaper.fieldInput('#c1', true)")
    check(wait_for(lambda: saved_answer("#tick") is not None, 3), "a tick too")
    check(saved_answer("#tick").question == "I have read this", "named by its label")
    wait_for(lambda: saved_answer("#twin~2") is not None, 3)
    check(saved_answer("#twin").text == "Twin one" and saved_answer("#twin~2").text == "Twin two", "two fields with one name stay apart")
    long_answer = "A long answer. " * 40
    tall = field("#a1")["height"]
    type_in("#a1", long_answer)
    check(wait_for(lambda: field("#a1")["height"] > tall and field("#a1")["height"] >= field("#a1")["scroll"], 3), "the field grows with the text")
    check(wait_for(lambda: field("#a1")["onPage"], 3), "and stays on the page")
    filler_answer = "A very long answer. " * 900
    type_in("#a1", filler_answer)
    check(wait_for(lambda: field("#a1")["onPage"], 3), "also when it no longer fits there")
    longest = field("#a1")
    check(longest["height"] <= longest["windowHeight"] * 0.6 + 1 and longest["scroll"] > longest["height"], "then it scrolls inside, so that nothing is cut off")
    type_in("#a1", "Typed just before closing")
    window.navigation.pop()
    check(wait_for(lambda: window.navigation.get_visible_page() is shelf), "closing the book at once")
    check(saved_answer("#field-1").text == "Typed just before closing", "loses nothing")
    check(study_path.read_bytes() == before_bytes, "and the book itself is never changed")
    open_study()
    check(wait_for(lambda: field("#a1")["value"] == "Typed just before closing", 3), "the answer is there again")
    check(field("#a2")["value"] == "Twin one" and field("#a3")["value"] == "Twin two" and field("#t1")["value"] == "A line", "all of them")
    check(field("#c1")["checked"] is True, "and the tick")
    js(reader, "endpaper.goToIndex(1)")
    wait_for(lambda: "Two" in js(reader, "endpaper.diagnostics()")["visibleText"], 5)
    type_in("#b2", "In the next chapter")
    check(wait_for(lambda: saved_answer("#field-2") is not None, 3), "fields of other chapters work")
    js(reader, "endpaper.goToIndex(0)")
    check(wait_for(lambda: "One" in js(reader, "endpaper.diagnostics()")["visibleText"], 5) and wait_for(lambda: field("#a1")["value"] == "Typed just before closing", 3), "and turning back still shows the answer")
    study_file = study_path.with_name("study.notes.md")
    reader._save_answers()
    study_file.write_text(study_file.read_text(encoding="utf-8").replace("Twin one", "Edited elsewhere"), encoding="utf-8")
    check(wait_for(lambda: field("#a2")["value"] == "Edited elsewhere", 5), "what changes in the file reaches the field")
    js(reader, "endpaper.fieldInput('#c1', false)")
    check(wait_for(lambda: saved_answer("#tick") is None, 3), "removing a tick removes it from the file")
    type_in("#a3", "")
    check(wait_for(lambda: saved_answer("#twin~2") is None, 3), "an empty field does too")
    check(wait_for(lambda: field("#a3")["value"] == "", 1), "even with words of the book in it")
    js(reader, "endpaper.fieldFocus('#a2', true)")
    check(wait_for(lambda: reader._in_field, 3), "the app knows the cursor is in a field")
    z_key = reader._on_key(None, Gdk.KEY_z, 0, Gdk.ModifierType.CONTROL_MASK)
    check(z_key is False, "Ctrl+Z there belongs to the field")
    js(reader, "endpaper.fieldFocus('#a2', false)")
    check(wait_for(lambda: not reader._in_field, 3), "and it knows when it left")
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    orphan = '> A question that is gone\n\n<!-- endpaper-answer field=gone.xhtml%23old kind=text -->\nAn answer without a field\n<!-- endpaper-answer-end -->\n'
    study_file.write_text(study_file.read_text(encoding="utf-8") + "\n" + orphan, encoding="utf-8")
    open_study()
    reader.notes_button.set_active(True)
    panel = reader.notes_panel
    def panel_texts():
        found, stack = [], [panel.content]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Label):
                found.append(widget.get_label())
            child = widget.get_first_child()
            while child:
                stack.append(child)
                child = child.get_next_sibling()
        return found
    check(wait_for(lambda: any("An answer without a field" in t for t in panel_texts()), 5), "an answer without a field is shown in the flyout")
    check(not any("Edited elsewhere" in t for t in panel_texts()), "one with a field is not")
    check("An answer without a field" in study_file.read_text(encoding="utf-8"), "and it stays in the file")
    reader.notes_button.set_active(False)
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    shelf.open_card(was_book)
    check(wait_for(lambda: reader.opened), "back to the other book")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))

    print("Bookmarks")
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    make_epub(LIB / "bookmarks.epub", title="Bookmark Book", identifier="urn:bm")
    check(wait_for(lambda: (LIB / "bookmarks.epub") in shelf.sections[LIB].cards), "a book for bookmarks")
    bm_card = shelf.sections[LIB].cards[LIB / "bookmarks.epub"]
    wait_for(lambda: bm_card.info is not None)
    shelf.open_card(bm_card)
    check(wait_for(lambda: reader.opened), "opens")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    bm_state = lambda: js(reader, "endpaper.bookmarkState()")
    saved_bm = lambda: NotesFile(LIB / "bookmarks.epub", "Bookmark Book").bookmarks
    check(reader.bookmark_pages.get_visible_child_name() == "empty", "no bookmarks at first, and the list says so")
    window.lookup_action("toggle-bookmark").activate(None)
    check(wait_for(lambda: len(saved_bm()) == 1, 3), "Ctrl+D sets a bookmark")
    check(saved_bm()[0].chapter == "Chapter 1" and saved_bm()[0].quote, "with its chapter and the beginning of the paragraph")
    check(wait_for(lambda: len(bm_state()["signs"]) == 1, 3), "and a sign beside the text")
    sign = bm_state()["signs"][0]
    check(sign["x"] > sign["textRight"], "in the margin, not on the text")
    check(reader.bookmark_pages.get_visible_child_name() == "list" and reader.bookmark_list.get_row_at_index(0) is not None, "it is in the list")
    window.lookup_action("toggle-bookmark").activate(None)
    check(wait_for(lambda: saved_bm() == [], 3), "Ctrl+D once more removes it")
    check(wait_for(lambda: bm_state()["signs"] == [], 3), "and its sign")
    reader._undo_mark()
    check(wait_for(lambda: len(saved_bm()) == 1 and len(bm_state()["signs"]) == 1, 3), "Ctrl+Z brings it back")
    sign = bm_state()["signs"][0]
    js(reader, f"endpaper.clickAt({sign['x'] + 3}, {sign['y'] + 3})")
    check(wait_for(lambda: saved_bm() == [], 3), "a click on the sign removes it")
    check(not reader.mark_popover.get_visible(), "without opening anything")
    reader._undo_mark()
    wait_for(lambda: len(saved_bm()) == 1, 3)
    first_bm = saved_bm()[0]
    window.lookup_action("toggle-bookmark").activate(None)
    wait_for(lambda: saved_bm() == [], 3)
    reader.bookmark_button.set_active(True)
    check(wait_for(lambda: bm_state()["placing"], 3) and reader.mark_done.get_visible() and reader.mark_hint.get_visible(), "the button asks where")
    spin(0.3)
    check(saved_bm() == [], "and sets nothing by itself")
    js(reader, 'endpaper.clickWord("p:nth-of-type(4)", "Sentence")')
    check(wait_for(lambda: len(saved_bm()) == 1, 3), "a click on a paragraph sets it there")
    check(saved_bm()[0].quote.startswith("2 Sentence"), "with the beginning of that paragraph")
    check(not reader.bookmark_button.get_active(), "and that is that")
    check(wait_for(lambda: not bm_state()["placing"], 3), "clicks are ordinary again")
    moved_cfi = saved_bm()[0].cfi
    js(reader, 'endpaper.clickWord("p:nth-of-type(6)", "Sentence")')
    spin(0.3)
    check(len(saved_bm()) == 1, "another click changes nothing")
    reader.bookmark_button.set_active(True)
    wait_for(lambda: bm_state()["placing"], 3)
    js(reader, 'endpaper.clickWord("p:nth-of-type(4)", "Sentence")')
    wait_for(lambda: not bm_state()["placing"], 3)
    spin(0.3)
    check(len(saved_bm()) == 1, "a paragraph with a bookmark keeps the one it has")
    reader.bookmark_button.set_active(True)
    wait_for(lambda: bm_state()["placing"], 3)
    reader._on_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
    check(not reader.bookmark_button.get_active() and wait_for(lambda: not bm_state()["placing"], 3), "Esc ends it")
    check(len(saved_bm()) == 1, "without setting anything")
    reader.mark_buttons["blue"].set_active(True)
    reader.stop_marking()
    js(reader, "endpaper.goToIndex(1)")
    wait_for(lambda: "Chapter 2" in js(reader, "endpaper.diagnostics()")["visibleText"], 5)
    rows = reader.bookmark_list
    wanted = next(row for row in (rows.get_row_at_index(i) for i in range(3)) if row and row.bookmark.cfi == moved_cfi)
    reader.notes_button.set_active(True)
    reader.notes_stack.set_visible_child_name("bookmarks")
    check(wait_for(lambda: wanted.get_mapped(), 3), "the list is in the flyout beside the notes")
    check(reader.bookmark_list.get_row_at_index(0).get_header() is not None, "by chapter, like the notes")
    rows.emit("row-activated", wanted)
    check(wait_for(lambda: "Chapter 1" in js(reader, "endpaper.diagnostics()")["visibleText"], 5), "a row goes to its bookmark")
    check(wait_for(lambda: any(s["onPage"] for s in bm_state()["signs"]), 3), "and its sign shows there")
    wanted.remove_button.emit("clicked")
    check(wait_for(lambda: all(b.cfi != moved_cfi for b in saved_bm()), 3), "the bin in the list removes it")
    reader.notes_stack.set_visible_child_name("notes")
    reader.notes_button.set_active(False)
    file_bm = (LIB / "bookmarks.notes.md")
    with file_bm.open("a", encoding="utf-8") as out:
        out.write(f"\n> By hand\n\n<!-- endpaper-bookmark id=byhand1 cfi={first_bm.cfi} -->\n")
    check(wait_for(lambda: any(b.quote == "By hand" for b in reader.notes.bookmarks), 5), "a bookmark from outside arrives")
    check(wait_for(lambda: bool(bm_state()["signs"]), 3), "and is drawn")
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    shelf.open_card(bm_card)
    check(wait_for(lambda: reader.opened), "opens again")
    check(wait_for(lambda: reader.bookmark_list.get_row_at_index(0) is not None, 5), "the bookmarks are there again")
    window.navigation.pop()
    wait_for(lambda: window.navigation.get_visible_page() is shelf)
    shelf.open_card(was_book)
    check(wait_for(lambda: reader.opened), "back to the other book")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))

    print("Fine tuning")
    check(not os.environ.get("ENDPAPER_CODE") and Gtk.IconTheme.get_for_display(window.get_display()).has_icon("studio.kleinod.Endpaper") or bool(os.environ.get("ENDPAPER_CODE")), "the logo is found for the About window")
    check(reader.menu_button.get_mapped() and reader.menu_button.get_primary(), "the main menu is there while reading")
    check(reader.settings_button.get_mapped() and reader.settings_button.is_ancestor(reader.overlay), "text and layout are in the bar below the text")
    check(len(reader.theme_buttons) == 5 and len(reader.mark_buttons) == 5, "five themes and five colors")
    reader.theme_buttons["sage"].set_active(True)
    check(wait_for(lambda: js(reader, "endpaper.bookDocument()")["background"] == "rgb(228, 236, 223)"), "a theme button reaches the book")
    check(config.reader_settings()["theme"] == "sage" and not reader.theme_follow.get_active(), "and is remembered")
    reader.theme_buttons["black"].set_active(True)
    check(wait_for(lambda: js(reader, "endpaper.bookDocument()")["background"] == "rgb(0, 0, 0)"), "black reaches the book")
    reader.theme_follow.set_active(True)
    check(config.reader_settings()["theme"] == "auto", "following the system can be chosen")
    expected = "dark" if Adw.StyleManager.get_default().get_dark() else "light"
    check(reader.theme_buttons[expected].get_active(), "and shows which theme that is")
    reader.theme_follow.set_active(False)
    check(config.reader_settings()["theme"] == expected, "leaving it keeps that look")
    reader.theme_buttons["light"].set_active(True)
    check(MARK_COLORS["purple"] and reader.mark_buttons["purple"].get_tooltip_text(), "the fifth color has a name")
    panel = reader.notes_panel
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: panel.cards)
    with_note = next(e for e in reader.notes.entries if e.has_note)
    without = next(e for e in reader.notes.entries if not e.has_note)
    check(with_note.id in panel.cards and without.id in panel.cards, "all marks are shown at first")
    check(panel.cards[with_note.id].has_css_class(f"note-tint-{with_note.color}"), "the box has the color of its mark")
    panel.only_notes.set_active(True)
    check(with_note.id in panel.cards and without.id not in panel.cards, "the switch leaves the marks with notes")
    check(config.reader_settings()["notes_only"] is True, "the switch is remembered")
    panel.show_entry(without.id, new_note=True)
    check(without.id in panel.cards, "a mark asked for by name is shown even so")
    panel.only_notes.set_active(False)
    panel.only_notes.set_active(True)
    check(without.id not in panel.cards, "and goes again with the switch")
    panel.only_notes.set_active(False)
    empty_row = (panel.add_empty_row(without.id), next(r for r in panel.rows if r.note_id is None))[1]
    empty_row.remove_button.emit("clicked")
    check(panel.remove_dialog is None and empty_row not in panel.rows, "an empty field goes without asking")
    written = next(r for r in panel.rows if r.note_id)
    text, kept = written.view.text, len(panel.rows)
    written.remove_button.emit("clicked")
    dialog = panel.remove_dialog
    check(dialog is not None and len(panel.rows) == kept, "a written note asks first")
    dialog.emit("response", "cancel")
    dialog.force_close()
    entry = next(e for e in reader.notes.entries if e.id == written.entry_id)
    check(entry.note(written.note_id).text == text and written in panel.rows, "Cancel keeps it")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())
    noted = next(e for e in reader.notes.entries if e.has_note)
    texts = [n.text for n in noted.notes]
    reader._open_mark = noted.id
    reader.popover_remove.emit("clicked")
    dialog = reader.remove_dialog
    check(dialog is not None and any(e.id == noted.id for e in reader.notes.entries), "a mark with notes asks first")
    dialog.emit("response", "cancel")
    dialog.force_close()
    check(any(e.id == noted.id for e in NotesFile(LIB / "marks.epub").entries), "Cancel keeps it")
    reader._open_mark = noted.id
    reader.popover_remove.emit("clicked")
    dialog = reader.remove_dialog
    dialog.emit("response", "remove")
    dialog.force_close()
    check(all(e.id != noted.id for e in NotesFile(LIB / "marks.epub").entries), "Remove takes it away")
    undo_key()
    back = next((e for e in NotesFile(LIB / "marks.epub").entries if e.id == noted.id), None)
    check(back is not None and [n.text for n in back.notes] == texts, "Ctrl+Z brings it back with its notes")
    spin(0.3)

    print("Marks and the outside world")
    long_note = "This note was written earlier and has to wrap over several lines in the flyout. " * 4
    reader.notes.add_note(reader.notes.entries[0].id, long_note.strip())
    window.navigation.pop()
    wait_for(lambda: not reader.opened)
    kept = [e.id for e in NotesFile(LIB / "marks.epub").entries]
    check(reader.notes is None, "closing the book lets go of the notes")
    with open(notes_file, "a", encoding="utf-8") as out:
        out.write("\nWritten by hand\n\n<!-- endpaper: id=bad cfi=epubcfi(/6/999!/4/2) color=yellow style=highlight -->\n")
    shelf.open_card(marked)
    check(wait_for(lambda: reader.opened), "the book opens with a mark that does not fit")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    check(wait_for(lambda: state()["drawn"] >= 3), "marks are there again after opening")
    check([e.id for e in reader.notes.entries][:len(kept)] == kept, "all of them, also the mark from before")
    window.lookup_action("toggle-notes").activate(None)
    check(wait_for(lambda: reader.notes_panel.cards), "the flyout opens")

    def nothing_cut_off():
        for note_row in reader.notes_panel.rows:
            end = note_row.view.get_iter_location(note_row.view.get_buffer().get_end_iter())
            if end.y + end.height > note_row.view.get_height():
                return False
        return bool(reader.notes_panel.rows)
    spin(0.5)
    check(wait_for(nothing_cut_off, 5), "notes from before are not cut off when the flyout opens")
    heights = [(r.view.get_height(), r.view.get_iter_location(r.view.get_buffer().get_end_iter()).y) for r in reader.notes_panel.rows]
    print("  note heights", heights)
    check("Written by hand" not in [e.quote for e in reader.notes.entries] and any("Written by hand" in text for text in reader.notes.loose + [" ".join(n.text for e in reader.notes.entries for n in e.notes)]), "text written by hand is kept")
    typing = next(iter(reader.notes_panel.cards))
    reader.notes_panel.add_empty_row(typing)
    field = next(r for r in reader.notes_panel.rows if r.entry_id == typing and r.note_id is None)
    field.view.get_buffer().set_text("Typed while the file changes")
    with open(notes_file, "a", encoding="utf-8") as out:
        out.write("\nAlso by hand\n")
    check(wait_for(lambda: "Typed while the file changes" in notes_file.read_text(encoding="utf-8") and "Also by hand" in notes_file.read_text(encoding="utf-8")), "typing and a change from outside both stay")
    window.lookup_action("toggle-notes").activate(None)
    wait_for(lambda: not reader.notes_split.get_show_sidebar())
    drawn = state()["drawn"]
    text = notes_file.read_text(encoding="utf-8")
    first = next(e for e in reader.notes.entries if not e.notes)
    start = text.index(f"id={first.id} ")
    line_end = text.index("\n", start)
    notes_file.write_text(text[:text.rindex("\n", 0, start) + 1] + text[line_end + 1:], encoding="utf-8")
    check(wait_for(lambda: all(e.id != first.id for e in reader.notes.entries)), "a change from outside is noticed")
    check(wait_for(lambda: state()["drawn"] == drawn - 1), "and shows in the text")
    check((LIB / "marks.epub").read_bytes() == book_bytes, "the book itself is unchanged")
    reader.show_message = window.show_toast
    window.navigation.pop()
    wait_for(lambda: not reader.opened)

    print("Pictures stay whole")
    make_figures_epub(LIB / "figures.epub")
    check(wait_for(lambda: (LIB / "figures.epub") in shelf.sections[LIB].cards), "the book with pictures is on the shelf")
    figures = shelf.sections[LIB].cards[LIB / "figures.epub"]
    wait_for(lambda: figures.info.title == "Figures")
    shelf.open_card(figures)
    check(wait_for(lambda: reader.opened), "it opens")
    wait_for(lambda: (js(reader, "endpaper.diagnostics()") or {}).get("cfi"))
    spin(0.5)
    images = js(reader, "endpaper.splitImages()")
    check(images["images"] == 30 and images["split"] == 0, f"no picture is cut in two ({images['split']} of {images['images']})")

    print("A picture shown large")
    def opened_picture():
        return wait_for(lambda: reader.picture_dialog is not None and reader.picture_dialog.get_mapped(), 5)
    clicked = js(reader, "endpaper.pictureClick(0)")
    check(clicked["taken"] and opened_picture(), "a click on a picture shows it large")
    dialog = reader.picture_dialog
    picture = dialog.get_child().get_content()
    check(picture.get_paintable().get_width() > 0, "drawn anew for the app")
    fills = lambda: max(picture.get_width() / window.get_width(), picture.get_height() / window.get_height())
    check(wait_for(lambda: fills() > 0.7), f"and large ({fills():.0%} of the window)")
    if os.environ.get("ENDPAPER_SHOT"):
        spin(0.5)
        paintable = Gtk.WidgetPaintable(widget=window)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        window.get_renderer().render_texture(node, None).save_to_png(os.environ["ENDPAPER_SHOT"])
    dialog.close()
    check(wait_for(lambda: not dialog.get_mapped()), "it closes")
    reader.picture_dialog = None
    check(clicked["tabbable"], "a picture can be reached with the Tab key")
    check(js(reader, "endpaper.pictureClick(0, true)")["taken"] and opened_picture(), "and opened with Enter")
    reader.picture_dialog.close()
    spin(0.5)
    reader.picture_dialog = None
    reader.toggle_marking()
    spin(0.3)
    js(reader, "endpaper.pictureClick(0)")
    spin(1)
    check(reader.picture_dialog is None, "in marking mode a click on a picture opens nothing")
    reader.toggle_marking()
    window.navigation.pop()
    wait_for(lambda: not reader.opened)

    print("A hostile book")
    Canary.hits.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Canary)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    make_hostile_epub(LIB / "hostile.epub", f"http://127.0.0.1:{server.server_address[1]}")
    check(wait_for(lambda: (LIB / "hostile.epub") in shelf.sections[LIB].cards), "the hostile book is on the shelf")
    hostile = shelf.sections[LIB].cards[LIB / "hostile.epub"]
    check(wait_for(lambda: hostile.info.title == "Hostile"), "its data is read")
    links = []
    reader.show_external_link = links.append
    shelf.open_card(hostile)
    check(wait_for(lambda: reader.opened), "it opens")
    spin(3)
    inside = js(reader, "endpaper.bookDocument()")
    check("hostile test" in inside["text"], "its text is shown")
    check(inside["pwned"] is None, "its code did not run inside the book")
    check(js(reader, "endpaper.diagnostics()")["pwned"] is None, "its code did not reach the app")
    for index in range(inside["links"]):
        js(reader, f"endpaper.clickLink({index})")
    spin(2)
    check(not [h for h in Canary.hits if h != "/click"], f"nothing left the app (seen: {Canary.hits})")
    check(not any(link.startswith(("javascript:", "file:")) for link in links), "script and file links go nowhere")
    check(any(link.endswith("/click") for link in links), "a web link only ever asks first")
    check(js(reader, "endpaper.diagnostics()")["blocked"] > 0, "the protection reported what it blocked")
    window.navigation.pop()
    wait_for(lambda: not reader.opened)

    print("Categories")
    shelf._attempt(lambda: library.rename_category(studium, "Studien"))
    check(wait_for(lambda: LIB / "Studien" in shelf.sections), "renaming a category follows the folder")
    if use_trash:
        shelf._trash([LIB / "Studien"], "x")
        check(wait_for(lambda: not (LIB / "Studien").exists() and LIB / "Studien" not in shelf.sections), "a category can go to the trash")
        check(wait_for(lambda: (trash / "Studien" / "d.epub").exists()), "with its books still in it")


def main():
    app = EndpaperApplication()
    status = {"code": 1}

    def start():
        window = app.get_active_window()
        try:
            scenario(app, window)
            status["code"] = 1 if failures else 0
        except Exception as error:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            failures.append(str(error))
        window.close()
        app.quit()
        return GLib.SOURCE_REMOVE

    app.connect("activate", lambda _a: GLib.timeout_add(300, start))
    app.set_flags(app.get_flags() | __import__("gi.repository.Gio", fromlist=["Gio"]).ApplicationFlags.NON_UNIQUE)
    app.run(None)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{passed} passed, {len(failures)} failed")
    for failure in failures:
        print(f"  - {failure}")
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
