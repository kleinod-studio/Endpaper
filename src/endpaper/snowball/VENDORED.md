# Vendored: Snowball stemmers

Upstream: the Python package `snowballstemmer` 3.1.1 from PyPI (sdist, sha256
`e07bbc54a0d798fe6010a12398422e62a8bfbba95c394fd0956ef58cb4d3e260`), generated from the
Snowball project, https://snowballstem.org. BSD-3-Clause (see `COPYING`).

Only the German and English stemmers are copied, unchanged, with the two modules they need:
`german_stemmer.py`, `english_stemmer.py`, `among.py` and `basestemmer.py`. The package's own
`__init__.py`, which imports every language, is left out; `book_index.py` imports the two
stemmers directly.

They give the search word forms: "Häuser" and "Haus" both have the stem "haus". Another language
means another stemmer from the same package, and a new search index (raise `SCHEMA_VERSION`).
