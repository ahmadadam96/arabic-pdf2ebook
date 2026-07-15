"""Structural validation for EPUBs produced by this package."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath
from posixpath import normpath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF_NS = "http://www.idpf.org/2007/opf"


class EpubValidationError(ValueError):
    """Raised when an EPUB archive has an invalid internal reference."""


def _fail(message: str) -> None:
    raise EpubValidationError(message)


def _read_xml(zf: zipfile.ZipFile, path: str, label: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(zf.read(path))
    except KeyError:
        _fail(f"missing {label}: {path}")
    except ElementTree.ParseError as exc:
        _fail(f"invalid XML in {label} {path}: {exc}")


def _resolve_local_href(source: str, href: str) -> tuple[str, str] | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    target = source if not parsed.path else normpath(
        str(PurePosixPath(source).parent / unquote(parsed.path))
    )
    return target, unquote(parsed.fragment)


def _require_target(names: set[str], source: str, href: str, context: str) -> tuple[str, str]:
    target = _resolve_local_href(source, href)
    if target is None:
        _fail(f"{context} has a non-local target: {href}")
    if target[0] not in names:
        _fail(f"{context} references missing file: {href}")
    return target


def validate_epub(path: Path) -> None:
    """Raise :class:`EpubValidationError` if *path* is structurally invalid."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if not infos:
                _fail("EPUB archive is empty")
            if infos[0].filename != "mimetype":
                _fail("first EPUB archive entry must be mimetype")
            if infos[0].compress_type != zipfile.ZIP_STORED:
                _fail("EPUB mimetype entry must be uncompressed")
            if zf.read("mimetype") != b"application/epub+zip":
                _fail("EPUB mimetype must be application/epub+zip")

            names = set(zf.namelist())
            container_path = "META-INF/container.xml"
            container = _read_xml(zf, container_path, "container document")
            rootfile = container.find(f".//{{{_CONTAINER_NS}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                _fail("container document has no rootfile full-path")
            opf_path = rootfile.attrib["full-path"]
            if opf_path not in names:
                _fail(f"container rootfile references missing OPF: {opf_path}")

            opf = _read_xml(zf, opf_path, "OPF package document")
            manifest: dict[str, ElementTree.Element] = {}
            for item in opf.findall(f"{{{_OPF_NS}}}manifest/{{{_OPF_NS}}}item"):
                item_id = item.get("id")
                href = item.get("href")
                if not item_id or not href:
                    _fail("OPF manifest item must have id and href attributes")
                manifest[item_id] = item
            for item_id, item in manifest.items():
                _require_target(names, opf_path, item.attrib["href"],
                                f"manifest item {item_id!r}")

            for itemref in opf.findall(f"{{{_OPF_NS}}}spine/{{{_OPF_NS}}}itemref"):
                item_id = itemref.get("idref")
                if item_id not in manifest:
                    _fail(f"spine references unknown manifest id: {item_id!r}")

            xhtml_ids: dict[str, set[str]] = {}
            xhtml_documents: dict[str, ElementTree.Element] = {}
            for item_id, item in manifest.items():
                if item.get("media-type") != "application/xhtml+xml":
                    continue
                xhtml_path, _ = _require_target(names, opf_path, item.attrib["href"],
                                                f"manifest item {item_id!r}")
                document = _read_xml(zf, xhtml_path, "XHTML document")
                ids = [element.attrib["id"] for element in document.iter() if "id" in element.attrib]
                if len(ids) != len(set(ids)):
                    _fail(f"XHTML document has duplicate IDs: {xhtml_path}")
                xhtml_ids[xhtml_path] = set(ids)
                xhtml_documents[xhtml_path] = document

            for source, document in xhtml_documents.items():
                for element in document.iter():
                    for attr in ("href", "src"):
                        href = element.get(attr)
                        if href is None or _resolve_local_href(source, href) is None:
                            continue
                        target_path, fragment = _require_target(
                            names, source, href, f"XHTML {attr} in {source}"
                        )
                        if fragment and fragment not in xhtml_ids.get(target_path, set()):
                            _fail(f"XHTML {attr} in {source} references missing fragment: {href}")

            for item_id, item in manifest.items():
                if item.get("media-type") != "application/x-dtbncx+xml":
                    continue
                ncx_path, _ = _require_target(names, opf_path, item.attrib["href"],
                                              f"manifest item {item_id!r}")
                ncx = _read_xml(zf, ncx_path, "NCX document")
                for content in ncx.iter():
                    if not content.tag.endswith("content") or "src" not in content.attrib:
                        continue
                    href = content.attrib["src"]
                    target_path, fragment = _require_target(
                        names, ncx_path, href, f"NCX content in {ncx_path}"
                    )
                    if fragment and fragment not in xhtml_ids.get(target_path, set()):
                        _fail(f"NCX content in {ncx_path} references missing fragment: {href}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubValidationError(f"invalid EPUB ZIP archive {path}: {exc}") from exc
