# Vendored: foliate-js

Upstream: https://github.com/johnfactotum/foliate-js, commit `78914ae` (2026-05-01), MIT (see `LICENSE`).
`vendor/zip.js` is zip.js by Gildas Lormeau, BSD-3-Clause.

`contrast.js` and `reader.js` beside this folder are ours.

Upstream calls the library unstable and has no releases, so it is pinned to one commit and updated on purpose only. Only the modules needed for EPUB are copied; PDF, MOBI, FB2, comics, OPDS and the demo reader are left out. After an update, run the tests, including the hostile book in `tests/gui_smoke.py`.
