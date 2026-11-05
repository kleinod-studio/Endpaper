"""Recognizes Markdown markup line by line, without GTK.

For a line it returns ranges (start, end, tag) that the editor styles with text tags.
Syntax characters get the "syntax" tag and are dimmed; the text in between is set
in bold, italic, as a heading and so on. Only the code block (```) spans multiple lines."""

import re

IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)[^)]*\)")

Span = tuple[int, int, str]

FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)[ \t]*([\w+#.-]*)")
_HEADING = re.compile(r"^(#{1,6})([ \t]+|$)")
_RULE = re.compile(r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^[ \t]*(?:>[ \t]?)+")
_LIST = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+)(\[[ xX]\][ \t]+)?")

_CODE = re.compile(r"(`+)(.+?)\1")
_IMAGE_OR_LINK = re.compile(r"(!?\[)([^\]\n]+)(\]\([^)\s]*(?:[ \t]+\"[^\"]*\")?\))")
# Footnotes: inline ^[text] (the note may hold links and other brackets one level deep),
# references [^label] and their definitions "[^label]: text" at the start of a line.
_INLINE_NOTE = re.compile(r"(\^\[)((?:[^\[\]\n]|\[[^\[\]\n]*\])*)(\])")
_NOTE_REFERENCE = re.compile(r"\[\^[^\]\s]+\](?!:)")
_NOTE_DEFINITION = re.compile(r"^\[\^[^\]\s]+\]:[ \t]?")
_AUTOLINK = re.compile(r"(<)((?:https?://|mailto:)[^>\s]+)(>)")
# A bare web address ends before trailing punctuation, so "see https://example.org." works.
_BARE_URL = re.compile(r"(?<![\w/])(?:https?://|www\.)[^\s<>]*[^\s<>.,;:!?\"')\]]")
_BARE_PREFIX = re.compile(r"(?:https?://)?(?:www\.)?")
_OPENABLE = ("http://", "https://", "mailto:")
_STRONG_EMPHASIS = re.compile(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)\1")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_EMPHASIS = re.compile(r"(?<![\w*])(\*|_)(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![\w*])")
_STRIKE = re.compile(r"(~~)(?=\S)(.+?)(?<=\S)~~")
_UNDERLINE = re.compile(r"(<u>)(.+?)(</u>)", re.IGNORECASE)  # Markdown has no underline; HTML workaround

_MASK = "\x00"  # hides already matched characters from the following patterns


def fence_language(line: str) -> str:
    """Language after the opening fence ("```python" -> "python"), otherwise ""."""
    match = FENCE.match(line)
    return match.group(2).lower() if match else ""


def list_text_start(line: str) -> int | None:
    """Where the text of a list item begins, after quote marks, indentation, the marker and a
    task box ("- [ ] "); None if the line is no list item. Wrapped lines are indented to here."""
    quote = _QUOTE.match(line)
    start = quote.end() if quote else 0
    item = _LIST.match(line[start:])
    if item is None or start + item.end() >= len(line):
        return None
    return start + item.end()


_TITLE_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TITLE_MARKUP = re.compile(r"<[^>]+>|[*_`~=]")
_TITLE_NOTE = re.compile(r"\^\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]|\[\^[^\]\s]+\]")


def outline(text: str) -> list[tuple[int, int, str]]:
    """The headings of a text as (line number, level, title), without those in code blocks.
    Titles are plain text: footnotes are left out, links keep their text, markup goes."""
    entries = []
    in_code_block = False
    for number, line in enumerate(text.split("\n")):
        if FENCE.match(line):
            in_code_block = not in_code_block
            continue
        heading = None if in_code_block else _HEADING.match(line)
        if heading is None:
            continue
        title = _TITLE_NOTE.sub("", line[heading.end():])
        title = _TITLE_MARKUP.sub("", _TITLE_LINK.sub(r"\1", title))
        title = " ".join(title.rstrip("#").split())
        if title:
            entries.append((number, len(heading.group(1)), title))
    return entries


def link_at(line: str, offset: int) -> str | None:
    """Address of the link at character offset in line, ready to open; None if there is none.

    Covers [text](address), <address> and bare web addresses. Images and links to anything
    but web pages and e-mail are left out."""
    for match in IMAGE.finditer(line):
        if match.start() <= offset < match.end():
            return None
    for match in _IMAGE_OR_LINK.finditer(line):
        if match.start() <= offset < match.end():
            address = re.match(r"\]\(([^)\s]*)", match.group(3)).group(1)
            return None if match.group(1) == "![" else _openable(address)
    for pattern, group in ((_AUTOLINK, 2), (_BARE_URL, 0)):
        for match in pattern.finditer(line):
            if match.start() <= offset < match.end():
                return _openable(match.group(group))
    return None


def link_spans(line: str) -> list[tuple[int, int, int, int]]:
    """The links of a line that is not inside a code block, as (start, start of the visible text,
    its end, end). Links inside code, images and footnotes do not count. Of a bare address, the
    prefix (https://, www.) folds away like the brackets of the others."""
    text = line
    for pattern in (_CODE, IMAGE, _INLINE_NOTE, _NOTE_REFERENCE):
        text = pattern.sub(lambda match: _MASK * len(match.group(0)), text)
    spans = []
    for pattern, group in ((_IMAGE_OR_LINK, 2), (_AUTOLINK, 2)):
        for match in pattern.finditer(text):
            if match.group(1) != "![":
                spans.append((match.start(), match.start(group), match.end(group), match.end()))
            text = text[: match.start()] + _MASK * (match.end() - match.start()) + text[match.end() :]
    for match in _BARE_URL.finditer(text):
        prefix = _BARE_PREFIX.match(match.group(0)).end()
        spans.append((match.start(), match.start() + prefix, match.end(), match.end()))
    return sorted(spans)


def note_spans(line: str) -> list[tuple[int, int, int, int]]:
    """The footnotes of a line that is not inside a code block, as (start, start of the note or
    label, its end, end): inline ones and references, but no definitions."""
    text = line
    for pattern in (_CODE, IMAGE):
        text = pattern.sub(lambda match: _MASK * len(match.group(0)), text)
    spans = [(match.start(), match.start(2), match.end(2), match.end()) for match in _INLINE_NOTE.finditer(text)]
    text = _INLINE_NOTE.sub(lambda match: _MASK * len(match.group(0)), text)
    spans += [(match.start(), match.start() + 2, match.end() - 1, match.end()) for match in _NOTE_REFERENCE.finditer(text)]
    return sorted(spans)


def note_definition(line: str) -> tuple[str, str] | None:
    """(label, text) of a line that defines a footnote, "[^label]: text"."""
    match = _NOTE_DEFINITION.match(line)
    return (line[2 : line.index("]")], line[match.end() :]) if match else None


def _openable(address: str) -> str | None:
    if address.startswith("www."):
        return "https://" + address
    return address if address.lower().startswith(_OPENABLE) else None


def line_spans(line: str, in_code_block: bool) -> list[Span]:
    """Styling of a line. in_code_block: the line lies between two ``` fences."""
    if FENCE.match(line):
        return [(0, len(line), "syntax")] if line else []
    if in_code_block:
        return [(0, len(line), "codeblock")] if line else []
    if _RULE.match(line):
        return [(0, len(line), "syntax")]

    heading = _HEADING.match(line)
    if heading:
        level = len(heading.group(1))
        spans = [(0, heading.end(), "syntax")]
        if heading.end() < len(line):
            spans.append((heading.end(), len(line), f"h{level}"))
        return spans + _inline(line, heading.end())

    definition = _NOTE_DEFINITION.match(line)
    if definition:
        spans = [(0, definition.end(), "syntax")]
        if definition.end() < len(line):
            spans.append((definition.end(), len(line), "footnote"))
        return spans + _inline(line, definition.end())

    spans: list[Span] = []
    start = 0
    quote = _QUOTE.match(line)
    if quote:
        spans.append((0, quote.end(), "syntax"))
        if quote.end() < len(line):
            spans.append((quote.end(), len(line), "quote"))
        start = quote.end()

    item = _LIST.match(line, start) if start == 0 else _LIST.match(line[start:])
    if item:
        offset = start
        marker_end = offset + item.end(3)
        spans.append((offset + item.start(2), offset + item.end(2), "list-marker"))
        if item.group(4):
            spans.append((marker_end, offset + item.end(4), "syntax"))
        start = offset + item.end()

    return spans + _inline(line, start)


def _inline(line: str, start: int) -> list[Span]:
    spans: list[Span] = []
    text = list(line)
    for i in range(start):
        text[i] = _MASK

    def masked() -> str:
        return "".join(text)

    def hide(a: int, b: int):
        for i in range(a, b):
            text[i] = _MASK

    for match in _CODE.finditer(masked()):
        ticks = len(match.group(1))
        spans += [
            (match.start(), match.start() + ticks, "syntax"),
            (match.start() + ticks, match.end() - ticks, "code"),
            (match.end() - ticks, match.end(), "syntax"),
        ]
        hide(match.start(), match.end())

    for match in _INLINE_NOTE.finditer(masked()):
        spans += [
            (match.start(1), match.end(1), "syntax"),
            (match.start(2), match.end(2), "footnote"),
            (match.start(3), match.end(3), "syntax"),
        ]
        hide(match.start(1), match.end(1))
        hide(match.start(3), match.end(3))

    for match in _NOTE_REFERENCE.finditer(masked()):
        spans.append((match.start(), match.end(), "syntax"))
        hide(match.start(), match.end())

    for match in IMAGE.finditer(masked()):
        # The caption stays text; address and attributes are markup.
        spans += [
            (match.start(), match.start("caption"), "syntax"),
            (match.end("caption"), match.end(), "syntax"),
        ]
        hide(match.start(), match.start("caption"))
        hide(match.end("caption"), match.end())

    for match in _IMAGE_OR_LINK.finditer(masked()):
        spans += [
            (match.start(1), match.end(1), "syntax"),
            (match.start(2), match.end(2), "link"),
            (match.start(3), match.end(3), "syntax"),
        ]
        hide(match.start(1), match.end(1))
        hide(match.start(3), match.end(3))

    for match in _AUTOLINK.finditer(masked()):
        spans += [
            (match.start(1), match.end(1), "syntax"),
            (match.start(2), match.end(2), "link"),
            (match.start(3), match.end(3), "syntax"),
        ]
        hide(match.start(), match.end())

    # Hidden afterwards, so underscores in an address do not turn into italics
    for match in _BARE_URL.finditer(masked()):
        spans.append((match.start(), match.end(), "link"))
        hide(match.start(), match.end())

    for match in _UNDERLINE.finditer(masked()):
        spans += [
            (match.start(1), match.end(1), "syntax"),
            (match.start(2), match.end(2), "underline"),
            (match.start(3), match.end(3), "syntax"),
        ]
        hide(match.start(1), match.end(1))
        hide(match.start(3), match.end(3))

    for pattern, tags in [
        (_STRONG_EMPHASIS, ("bold", "italic")),
        (_STRONG, ("bold",)),
        (_EMPHASIS, ("italic",)),
        (_STRIKE, ("strike",)),
    ]:
        for match in pattern.finditer(masked()):
            width = len(match.group(1))
            inner = (match.start() + width, match.end() - width)
            spans.append((match.start(), inner[0], "syntax"))
            spans.append((inner[1], match.end(), "syntax"))
            spans += [(inner[0], inner[1], tag) for tag in tags]
            hide(match.start(), inner[0])
            hide(inner[1], match.end())

    return spans
