# Roadmap

Endpaper is a hobby project without commercial intent. It grows in phases, and each phase has to be solid before the next one starts.

## Principles

- **Your notes are never lost.** Saves are atomic, and changes made by other programs never overwrite anything.
- **Plain files.** Books stay `.epub` files and notes become `.md` files beside them, all readable without Endpaper. A database is only ever a cache that can be thrown away.
- **Private.** No telemetry, no accounts, no network access, not even for AI features.
- **Books are not trusted.** Their scripts do not run and they cannot load anything from outside.
- **GNOME-native.** Standard libadwaita widgets and patterns.
- **Accessible.** Everything works with the keyboard, the interface is translatable, and color is never the only signal.

## Phase 1: Shelf and reading ✅

- [x] Library folder; subfolders are categories
- [x] Shelf with covers, categories that fold, and pinned books that stay visible
- [x] Create, rename and delete categories; add, move, rename and delete books; notes move along
- [x] Changes in the folder from outside appear on the shelf
- [x] Reading with mouse, scroll wheel and keyboard; pages (two side by side in a wide window) or continuous scrolling
- [x] Table of contents beside the text, and a way back after following a link
- [x] Reading position per book
- [x] Font, size, line spacing, text width, hyphenation; light, sepia and dark colors
- [x] Filter and sort the shelf
- [x] Clear message for files that cannot be opened

## Phase 2: Highlights and notes

- [x] Marking bar: choose a color, click the first and the last word, or double-click one word; no dragging
- [x] Click a mark to change its color, add a note or remove it
- [x] Notes panel beside the text: all marks by chapter, any number of notes in Markdown
- [x] Marking with the keyboard alone
- [x] Marks across pages
- [x] Notes saved as readable `.notes.md` files beside the book
- [x] Undo and redo for changes to marks
- [ ] Open in Typecase
- [ ] Previews of footnotes and references, without leaving the page

## Phase 3: Search

- [ ] Full-text search in a book, across the library and in your notes

## Phase 4: Intelligent search, on your computer

- [ ] Search by meaning, in German and English, with a small open-source model
- [ ] If the quality convinces: short answers with a small language model, based only on your books, with every source to jump to
- [ ] Models run locally only, and the app stays without network access

## Non-goals

- PDF, comics, MOBI and other formats, for now
- DRM, in any form
- Editing books
- A built-in shop, online catalogs, cloud sync or accounts (sync the folder with Nextcloud or Syncthing instead)
- Sync with Tolino or other e-readers
- Separate mobile apps, plugins
- One book in several categories at once: a file lies in exactly one folder

## Quality targets

- Starts in under two seconds.
- A shelf of 1,000 books stays fluid.
- A book opens in under a second, and turning a page has no noticeable delay.
