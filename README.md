# Endpaper

A friendly bookshelf and a quiet place to read, for Linux. Built with GTK 4, libadwaita and Python.

Your books stay plain `.epub` files in a folder of your choice, with one subfolder for each category: no database, no account, no network access. Endpaper never changes your books.

> **Status:** 0.4.1, an early version for everyday testing. The shelf, reading, marks and notes, and the search work. See the [roadmap](ROADMAP.md).

## Features

- **Shelf**: any folder becomes your library, and its subfolders become categories. Categories fold away to keep the start page tidy.
- **Pins**: pin books in each category. They come first, and stay visible while the category is folded. Categories you use most can be pinned to the top of the shelf.
- **Reading progress**: a small ring on the cover and in the corner of the open book shows how far you have read, the same ring as the writing goal in Typecase. It turns green when a book is finished. Books you look things up in rather than read through can be marked as reference works, and show no progress.
- **Finished books**: near the end of a book the card beside the ring asks whether you have finished it; the shelf menu marks a book as finished too. Finished books also stand together at the end of the shelf, and stay in their own category.
- **Order from the shelf**: create, rename and delete categories; add, rename and delete books; move books by dragging or with Alt+Up and Alt+Down. Deleting always goes through the system trash, and nothing is ever overwritten.
- **Changes from outside**: add a file with your file manager or a sync tool, and it appears on the shelf.
- **Reading**: pages or continuous scrolling, with two pages side by side when the window is wide enough, five page colors, turned with the mouse, the scroll wheel or the keyboard. Every book remembers where you were. Footnotes and references show in a small popup at their mark, on hover or tap, without leaving the page. A click on a picture shows it large.
- **Table of contents**: beside the text, with the current chapter highlighted (F9). After following a link, a button takes you back.
- **Focus mode**: F11 or Ctrl+Shift+Enter shows only the text and the bar below it, full screen, like focus mode in Typecase. The text spreads wider, with more room above and below. The header bar comes back at the top edge; Esc leaves.
- **Search in the book**: Ctrl+F lists every place by chapter, each with a short excerpt that shows your marks. Your notes and answers that match stand among them, in the order of the book. Enter goes from one to the next.
- **Search in the library**: the search on the shelf finds titles and authors, and also the words in the text of every book and in your notes. Each book shows its cover and its first passages, with your marks in them, and a click opens it right there. Other forms of a word count too, in German and English (house, houses), and a word with a typing error finds the closest one in your books. The text is read once in the background, into a cache that can always be thrown away.
- **Your way of reading**: font, text size, line spacing, text width, hyphenation, and light, sepia or dark colors that also apply to books with colors of their own.
- **Bookmarks**: Ctrl+D or a button below the text sets one on a paragraph. The list stands in the flyout beside your notes.
- **Answer fields**: text fields and tick boxes of study editions work. What you enter is saved in the notes file beside the book, and the book itself stays as it is.
- **Marking without dragging**: choose a color in the bar below the text, click the first word and then the last, across pages if you like. A double-click marks one word. It works with the keyboard alone too: Ctrl+M, the arrow keys and Enter. Click a mark to change it or to add a note. Five colors. Ctrl+Z takes back the last change to a mark, Ctrl+Shift+Z restores it.
- **Notes**: a panel beside the text lists all marks by chapter, each with any number of notes in Markdown, without a limit on their length. They are saved while you type, and a small box beside the first line of a mark shows that it has notes.
- **Find a book**: filter by title or author, and sort by title, author or recent reading.
- **GNOME-native**: libadwaita look, light and dark style, usable with the keyboard. The interface is in English and German.

Only EPUB is supported, and only without DRM.

## Your data

| What | Where | |
| --- | --- | --- |
| Books | Your library folder | Never changed by Endpaper |
| Pins, reference works, finished books | `.endpaper.json` in each category folder | Syncs along with the folder |
| Marks and notes | `<book>.notes.md` beside the book | Readable without Endpaper, moves and syncs with the book |
| Reading positions, folded categories | App data on this computer | Not synced |
| Covers | Cache | Can be deleted at any time |

## Privacy and safety

Endpaper has no network access at all: the Flatpak does not have the permission. Books are treated as untrusted content, so their scripts do not run and they cannot load anything from outside. A web link in a book opens in your browser only after asking you.

## Install

Download the bundle for your architecture from the [latest release](https://github.com/kleinod-studio/Endpaper/releases/latest) and install it:

```bash
flatpak install --user Endpaper-0.4.1-x86_64.flatpak
```

Use the `-aarch64` bundle on ARM computers. The GNOME 51 runtime comes from Flathub, which has to be set up once (`flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`).

## Build and run

From source, without installing (needs GTK 4, libadwaita, WebKitGTK 6.0 and PyGObject):

```bash
./run-dev.sh
```

As a Flatpak (needs `flatpak-builder` and the GNOME 51 runtime):

```bash
flatpak-builder --user --install --force-clean build-dir studio.kleinod.Endpaper.yml
```

## Tests

```bash
python3 -m unittest discover -s tests
```

```bash
python3 tests/gui_smoke.py
```

The second one drives the real interface and needs a desktop session, with a window that is really on screen. It only uses a temporary folder and its own configuration, and it includes a deliberately hostile book.

## License

GPL-3.0-or-later. Endpaper uses [foliate-js](https://github.com/johnfactotum/foliate-js) (MIT) with [zip.js](https://github.com/gildas-lormeau/zip.js) (BSD-3-Clause); see `src/endpaper/web/foliate-js/VENDORED.md`. Word forms come from the German and English stemmers of [Snowball](https://snowballstem.org) (BSD-3-Clause); see `src/endpaper/snowball/VENDORED.md`.

This project is developed with AI assistance (Claude).
