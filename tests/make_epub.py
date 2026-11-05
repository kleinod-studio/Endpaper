"""Builds small EPUBs for the tests. All text here is made up for this purpose."""

import struct
import zipfile
import zlib
from pathlib import Path



def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


# A valid 2x3 pixel PNG, so that cover handling is really exercised.
PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress((b"\x00" + b"\x40\x70\xa0" * 2) * 3))
    + _chunk(b"IEND", b"")
)


def chapter(title: str, paragraphs: int) -> str:
    text = " ".join(f"Sentence number {i} of the chapter called {title} goes on for a while." for i in range(12))
    body = "".join(f"<p>{n} {text}</p>" for n in range(paragraphs))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">'
        f"<head><title>{title}</title></head><body><h1>{title}</h1>"
        f'<p><a href="ch2.xhtml" id="to-two">To chapter two</a></p>{body}</body></html>'
    )


def make_epub(path: Path, title="Test Book", author="A. Author", identifier="urn:test:1", cover=True, chapters=3, paragraphs=40):
    items, spine, toc = [], [], []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/p.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for n in range(1, chapters + 1):
            z.writestr(f"OEBPS/ch{n}.xhtml", chapter(f"Chapter {n}", paragraphs))
            items.append(f'<item id="c{n}" href="ch{n}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{n}"/>')
            toc.append(f'<li><a href="ch{n}.xhtml">Chapter {n}</a></li>')
        z.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<body><nav epub:type="toc"><ol>{"".join(toc)}</ol></nav></body></html>',
        )
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        if cover:
            z.writestr("OEBPS/cover.png", PNG)
            items.append('<item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>')
        creator = f"<dc:creator>{author}</dc:creator>" if author else ""
        z.writestr(
            "OEBPS/p.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">{identifier}</dc:identifier>'
            f"<dc:title>{title}</dc:title>{creator}<dc:language>en</dc:language></metadata>"
            f'<manifest>{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>',
        )
    return path


def make_styled_epub(path: Path):
    """A book that insists on its own colors with !important, as many study editions do."""
    css = """
    html body h1#head { color: #2f5f63 !important; }
    html body p#gray { color: #555555 !important; }
    html body div#box { background-color: #eeeeee !important; }
    html body p#banner { background-color: #143066 !important; color: #ffffff !important; }
    html body a#link { color: #2a4a8f !important; }
    html body div#rule { border-left: 3px solid #143066 !important; }
    html body div#picbox { background-color: #cddbdc !important; }
    """
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en"><head><title>Styled</title>'
        f"<style>{css}</style></head><body>"
        '<h1 id="head">A dark heading</h1><p id="gray">Gray text that fades on a dark page.</p>'
        '<div id="box"><p id="inbox">Text in a light box.</p></div><p id="banner">White on a dark banner</p>'
        '<p><a id="link" href="ch2.xhtml">A link</a></p><div id="rule">Beside a rule</div>'
        '<div id="picbox" style="background-image: url(\'cover.png\'); background-position: bottom;'
        ' background-repeat: no-repeat"><p id="onpic">Text above a picture, on the light color the book gives for it.</p>'
        '<textarea id="picfield"></textarea></div>'
        '<img id="pic" src="cover.png" alt=""/></body></html>'
    )
    make_epub(path, title="Styled", identifier="urn:styled", chapters=2, paragraphs=3)
    with zipfile.ZipFile(path) as old:
        entries = {name: old.read(name) for name in old.namelist()}
    entries["OEBPS/ch1.xhtml"] = body.encode("utf-8")
    with zipfile.ZipFile(path, "w") as new:
        for name, data in entries.items():
            new.writestr(name, data)
    return path


def make_figures_epub(path: Path):
    """Pictures in floating figures between paragraphs, a layout that can split them across pages."""
    text = " ".join(f"Sentence number {i} goes on for a while beside the picture." for i in range(6))
    block = (
        '<div style="float: right; width: 50%;"><figure style="display: grid; margin: 0;">'
        '<img src="cover.png" alt="" style="display: block; width: 100%; height: auto;"/></figure></div>'
        f"<p>{text}</p><div style=\"clear: both;\"></div>"
    )
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en"><head><title>Figures</title></head>'
        f"<body><h1>Figures</h1>{block * 30}</body></html>"
    )
    make_epub(path, title="Figures", identifier="urn:figures", chapters=1, paragraphs=1)
    with zipfile.ZipFile(path) as old:
        entries = {name: old.read(name) for name in old.namelist()}
    entries["OEBPS/ch1.xhtml"] = body.encode("utf-8")
    with zipfile.ZipFile(path, "w") as new:
        for name, data in entries.items():
            new.writestr(name, data)
    return path


def make_notes_epub(path: Path):
    """Footnotes and references of the kinds books use, in text made up for this test."""
    filler = "".join(f"<p>Filler paragraph {n} with some words that fill the page for a while.</p>" for n in range(4))
    one = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head><title>One</title></head><body>'
        '<h1>One</h1>'
        '<p id="para">Main text<span id="src1"></span><a epub:type="noteref" href="#note1">note-mark</a> and '
        '<span id="src2"></span><a href="#plain2">[2]</a> and '
        '<a epub:type="noteref" href="#bad">bad-mark</a> and '
        '<a href="#target-para">same-chapter</a> and <a href="two.xhtml#verse2">verse-link</a> and '
        '<a href="two.xhtml#long-para">long-link</a> and <a href="two.xhtml#head">heading-link</a> and '
        '<a href="two.xhtml">chapter-link</a> and <a epub:type="noteref" href="#missing">nothing-mark</a> and '
        '<a href="https://example.org/x">web-link</a> and <a href="excerpt.xhtml#pc1"><strong>excerpt-link</strong></a> and '
        '<a href="excerpt.xhtml#pc2">second-excerpt</a>.</p>'
        f'{filler}<p id="target-para">A paragraph of its own, <em>worth</em> looking at.</p>'
        '<aside epub:type="footnote" id="aside1"><div epub:type="footnote" id="note1"><p><a href="#src1">*</a> '
        'The <strong>text</strong> of the first footnote.</p></div></aside>'
        '<p id="plain2"><a href="#src2">[2]</a> A plain footnote without any markup for it.</p>'
        '<aside epub:type="footnote"><div id="bad"><p>Harmless words'
        '<img src="x" onerror="window.__pwned=1"/><script>window.__pwned=2</script>'
        '<a href="javascript:window.__pwned=3">more words</a></p></div></aside>'
        '</body></html>'
    )
    two = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head><title>Two</title></head><body>'
        '<h1 id="head">Two</h1><p>The paragraph after the heading.</p>'
        '<p><span id="verse1"></span><strong><sup>1</sup></strong> First verse. '
        '<span id="verse2"></span><strong><sup>2</sup></strong> Second verse'
        '<span id="src9"></span><a epub:type="noteref" href="#note9">*</a> goes on. '
        '<span id="verse3"></span><strong><sup>3</sup></strong> Third verse.</p>'
        f'<aside epub:type="footnote"><div id="note9"><p>Another note.</p></div></aside>'
        f'<p id="long-para">{"A very long paragraph. " * 200}</p>'
        '</body></html>'
    )
    excerpt = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head><title>Excerpts</title></head><body>'
        '<div class="group"><div class="ext"><p><a href="one.xhtml#para">^ <span id="pc1"></span><strong>***First Source p. 3***</strong></a></p>'
        '<header><h1>Heading of the first excerpt</h1></header><p>Body of the first excerpt'
        '<span id="srcx"></span><a epub:type="noteref" href="#notex">*</a> goes on.</p>'
        '<aside epub:type="footnote"><div id="notex"><p>A footnote inside the excerpt.</p></div></aside></div>'
        '<div class="ext"><p><a href="one.xhtml#para">^ <span id="pc2"></span><strong>***Second Source***</strong></a></p>'
        '<p>Body of the second excerpt.</p></div></div></body></html>'
    )
    items, spine = [], []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/p.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for name, text in (("one", one), ("two", two), ("excerpt", excerpt)):
            z.writestr(f"OEBPS/{name}.xhtml", text)
            items.append(f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{name}"/>')
        z.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<body><nav epub:type="toc"><ol><li><a href="one.xhtml">One</a></li><li><a href="two.xhtml">Two</a></li></ol></nav></body></html>',
        )
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        z.writestr(
            "OEBPS/p.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">urn:notes</dc:identifier>'
            '<dc:title>Notes Book</dc:title><dc:language>en</dc:language></metadata>'
            f'<manifest>{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>',
        )
    return path


def make_answers_epub(path: Path):
    """Fields of a study edition, in text made up for this test."""
    one = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>One</title></head><body><h1>One</h1>'
        '<p>What is the first question?</p>'
        '<div><label for="a1" style="display:none">Answer</label><textarea id="a1" name="field-1"></textarea></div>'
        '<p>And the second?</p><div><textarea id="a2" name="twin"></textarea></div>'
        '<p>The same name once more?</p><div><textarea id="a3" name="twin">Words from the book</textarea></div>'
        '<div><input type="checkbox" id="c1" name="tick"/><label for="c1">I have read this</label></div>'
        '<p>A line?</p><input id="t1" name="line" type="text"/>'
        '<input id="r1" name="radio" type="radio"/><select id="s1"><option>x</option></select><button id="b1">Press</button>'
        + "".join(f"<p>Filler {n} with some words to fill the page for a while.</p>" for n in range(30)) +
        '</body></html>'
    )
    two = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>Two</title></head><body><h1>Two</h1>'
        '<p>Another question?</p><textarea id="b2" name="field-2"></textarea></body></html>'
    )
    items, spine = [], []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/p.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for name, text in (("one", one), ("two", two)):
            z.writestr(f"OEBPS/{name}.xhtml", text)
            items.append(f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{name}"/>')
        z.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<body><nav epub:type="toc"><ol><li><a href="one.xhtml">One</a></li><li><a href="two.xhtml">Two</a></li></ol></nav></body></html>',
        )
        items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        z.writestr(
            "OEBPS/p.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">urn:answers</dc:identifier>'
            '<dc:title>Study Book</dc:title><dc:language>en</dc:language></metadata>'
            f'<manifest>{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>',
        )
    return path


def make_hostile_epub(path: Path, base: str):
    """A book that tries everything to run code and to reach the outside. base: a local server that records requests."""
    leak = (
        f"fetch('{base}/fetch');new Image().src='{base}/js-image';"
        "document.documentElement.setAttribute('data-pwned','1');try{parent.__pwned=1}catch(e){}"
    )
    amp = leak.replace("&", "&amp;")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>
<meta http-equiv="refresh" content="1;url={base}/meta-refresh"/>
<base href="{base}/base/"/>
<link rel="stylesheet" href="{base}/link-css"/>
<style>@import url('{base}/import'); @font-face{{font-family:x;src:url('{base}/font')}} body{{background:url('{base}/bg');font-family:x}}</style>
<script>{leak}</script>
<script src="{base}/ext.js"></script>
<script src="evil.js"></script>
</head><body onload="{leak}"><h1>hostile test</h1>
<p>{"Filler text for the page. " * 30}</p>
<img src="{base}/img"/>
<img src="x-does-not-exist" onerror="{leak}"/>
<img src="file:///etc/hostname"/>
<img src="endpaper://app/../../../../etc/hostname"/>
<svg xmlns="http://www.w3.org/2000/svg" onload="{leak}"><script>{leak}</script><image href="{base}/svg-image"/></svg>
<iframe src="{base}/iframe"></iframe>
<iframe src="data:text/html,&lt;script&gt;{amp}&lt;/script&gt;"></iframe>
<iframe srcdoc="&lt;script&gt;{amp}&lt;/script&gt;"></iframe>
<object data="{base}/object"></object><embed src="{base}/embed"/>
<video src="{base}/video" autoplay="autoplay"></video><audio src="{base}/audio" autoplay="autoplay"></audio>
<form action="{base}/form" method="get"><input type="image" src="{base}/form-image"/></form>
<a href="{base}/click" ping="{base}/ping">external link</a>
<a href="javascript:{leak}">script link</a>
<a href="file:///etc/hostname">file link</a>
</body></html>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/p.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/p.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">urn:hostile</dc:identifier>'
            "<dc:title>Hostile</dc:title><dc:language>en</dc:language></metadata>"
            '<manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml" properties="scripted svg"/>'
            '<item id="n" href="n.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            '<item id="e" href="evil.js" media-type="text/javascript"/></manifest><spine><itemref idref="c"/></spine></package>',
        )
        z.writestr(
            "OEBPS/n.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<body><nav epub:type="toc"><ol><li><a href="c.xhtml">c</a></li></ol></nav></body></html>',
        )
        z.writestr("OEBPS/c.xhtml", body)
        z.writestr("OEBPS/evil.js", leak)
    return path
