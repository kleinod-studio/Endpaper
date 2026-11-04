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
from gi.repository import GLib  # noqa: E402

from endpaper import config, library  # noqa: E402
from endpaper.main import EndpaperApplication  # noqa: E402
from make_epub import make_epub, make_hostile_epub, make_styled_epub  # noqa: E402

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
    for theme in ("dark", "sepia"):
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
