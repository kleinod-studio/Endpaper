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
    """
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en"><head><title>Styled</title>'
        f"<style>{css}</style></head><body>"
        '<h1 id="head">A dark heading</h1><p id="gray">Gray text that fades on a dark page.</p>'
        '<div id="box"><p id="inbox">Text in a light box.</p></div><p id="banner">White on a dark banner</p>'
        '<p><a id="link" href="ch2.xhtml">A link</a></p><div id="rule">Beside a rule</div>'
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
