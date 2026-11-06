"""Reads a book's title, author and cover without unpacking it.

Books are not trusted: sizes are limited, entity declarations are refused, and paths
only ever name entries inside the archive. Nothing is written to disk here."""

import hashlib
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_COVER_BYTES = 40 * 1024 * 1024
_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF = "http://www.idpf.org/2007/opf"
_DC = "http://purl.org/dc/elements/1.1/"
_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


class EpubError(Exception):
    """The file is not a readable EPUB."""


@dataclass
class EpubInfo:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = ""
    identifier: str = ""
    cover_path: str | None = None


def _read_limited(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise EpubError(f"Missing entry: {name}") from error
    if info.file_size > limit:
        raise EpubError(f"Entry too large: {name}")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)  # the declared size can be a lie
    if len(data) > limit:
        raise EpubError(f"Entry too large: {name}")
    return data


def _parse_xml(data: bytes) -> ET.Element:
    if b"<!ENTITY" in data:
        raise EpubError("XML entity declarations are not allowed")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise EpubError("Broken XML") from error


def _text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _find_cover(package: ET.Element, base: str, names: set[str]) -> str | None:
    items = package.findall(f"{{{_OPF}}}manifest/{{{_OPF}}}item")

    def resolve(item: ET.Element) -> str | None:
        href = item.get("href")
        if not href:
            return None
        path = posixpath.normpath(posixpath.join(base, unquote(href)))
        return path if path in names else None

    for item in items:  # EPUB 3
        if "cover-image" in (item.get("properties") or "").split():
            if found := resolve(item):
                return found
    for meta in package.findall(f"{{{_OPF}}}metadata/{{{_OPF}}}meta"):  # EPUB 2
        if meta.get("name") == "cover":
            for item in items:
                if item.get("id") == meta.get("content"):
                    if found := resolve(item):
                        return found
    for item in items:  # common naming, as a last resort
        marker = f"{item.get('id', '')} {item.get('href', '')}".lower()
        if "cover" in marker and item.get("media-type") in _IMAGE_TYPES:
            if found := resolve(item):
                return found
    return None


def read_info(path: Path) -> EpubInfo:
    """Raises EpubError if the file cannot be read as an EPUB."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            container = _parse_xml(_read_limited(archive, "META-INF/container.xml", MAX_XML_BYTES))
            rootfile = container.find(f".//{{{_CONTAINER}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise EpubError("No package document")
            opf_path = rootfile.get("full-path")
            package = _parse_xml(_read_limited(archive, opf_path, MAX_XML_BYTES))
            metadata = package.find(f"{{{_OPF}}}metadata")
            if metadata is None:
                raise EpubError("No metadata")
            info = EpubInfo(
                title=_text(metadata.find(f"{{{_DC}}}title")),
                authors=[a for a in (_text(c) for c in metadata.findall(f"{{{_DC}}}creator")) if a],
                language=_text(metadata.find(f"{{{_DC}}}language")),
                identifier=_text(metadata.find(f"{{{_DC}}}identifier")),
                cover_path=_find_cover(package, posixpath.dirname(opf_path), names),
            )
            return info
    except (zipfile.BadZipFile, OSError, UnicodeError, ValueError, RuntimeError, NotImplementedError) as error:
        raise EpubError(str(error)) from error


MAX_SPINE_ITEMS = 10_000


def read_spine(path: Path):
    """The documents of the reading order, as bytes, one at a time: (place in the spine, data or None).
    None stands for a document that is missing or too large, so that the places stay those of the reader."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            container = _parse_xml(_read_limited(archive, "META-INF/container.xml", MAX_XML_BYTES))
            rootfile = container.find(f".//{{{_CONTAINER}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise EpubError("No package document")
            opf_path = rootfile.get("full-path")
            package = _parse_xml(_read_limited(archive, opf_path, MAX_XML_BYTES))
            base = posixpath.dirname(opf_path)
            hrefs = {
                item.get("id"): posixpath.normpath(posixpath.join(base, unquote(item.get("href") or "")))
                for item in package.findall(f"{{{_OPF}}}manifest/{{{_OPF}}}item")
            }
            # Like the reader, which leaves out what the manifest does not know.
            known = [ref for ref in package.findall(f"{{{_OPF}}}spine/{{{_OPF}}}itemref") if ref.get("idref") in hrefs]
            for place, itemref in enumerate(known[:MAX_SPINE_ITEMS]):
                name = hrefs[itemref.get("idref")]
                data = None
                if name in names:
                    try:
                        data = _read_limited(archive, name, MAX_XML_BYTES)
                    except EpubError:
                        data = None
                yield place, data
    except (zipfile.BadZipFile, OSError, UnicodeError, ValueError, RuntimeError, NotImplementedError) as error:
        raise EpubError(str(error)) from error


def read_cover(path: Path, cover_path: str) -> bytes:
    try:
        with zipfile.ZipFile(path) as archive:
            return _read_limited(archive, cover_path, MAX_COVER_BYTES)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as error:
        raise EpubError(str(error)) from error


def book_key(info: EpubInfo, fallback_name: str) -> str:
    """Stays the same when the file is moved or renamed, so that the reading position follows it."""
    source = f"{info.identifier}\n{info.title}" if info.identifier else fallback_name
    return hashlib.sha1(source.encode("utf-8")).hexdigest()
