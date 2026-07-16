from __future__ import annotations

import logging
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from app.sources.base import LoadedSource, SourceLoader, SourceLocation, SourceSegment
from app.sources.text import segments_from_markdown

logger = logging.getLogger(__name__)

_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_DOCUMENT_MEDIA_TYPES = {
    "application/xhtml+xml",
    "application/x-html+xml",
    "text/html",
    "text/x-html",
}


def _local_name(tag: str) -> str:
    return tag.rpartition("}")[-1] if tag else ""


def _chapter_text(html: str) -> str:
    import trafilatura

    extracted = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    if extracted and extracted.strip():
        return extracted.strip()

    from html.parser import HTMLParser

    class _TextCollector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            text = data.strip()
            if text:
                self.parts.append(text)

    parser = _TextCollector()
    parser.feed(html)
    return "\n".join(parser.parts).strip()


def _resolve_epub_member(opf_path: str, href: str, archive_names: set[str]) -> str | None:
    """Resolve a manifest href to a ZIP member path."""
    href = unquote((href or "").strip())
    if not href:
        return None

    opf_dir = PurePosixPath(opf_path).parent.as_posix()
    if opf_dir == ".":
        opf_dir = ""

    candidates: list[str] = []

    def add(candidate: str) -> None:
        normalized = PurePosixPath(candidate).as_posix()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    if href.startswith("/"):
        add(href.lstrip("/"))
    else:
        if opf_dir:
            add(str(PurePosixPath(opf_dir) / href))
        add(href)
        # Some publishers use paths anchored at the publication root instead of the OPF folder.
        if opf_dir and href.split("/", 1)[0] == PurePosixPath(opf_dir).parts[0]:
            add(href)

    for candidate in candidates:
        if candidate in archive_names:
            return candidate

    basename = PurePosixPath(href).name
    matches = [
        name
        for name in archive_names
        if name == basename or name.endswith(f"/{basename}")
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_opf(
    opf_bytes: bytes,
) -> tuple[str, dict[str, tuple[str, str, list[str]]], list[str]]:
    root = ET.fromstring(opf_bytes)
    title = ""
    for element in root.findall(".//dc:title", _OPF_NS):
        if element.text and element.text.strip():
            title = element.text.strip()
            break
    if not title:
        for element in root.iter():
            if _local_name(element.tag) == "title" and element.text and element.text.strip():
                title = element.text.strip()
                break

    manifest: dict[str, tuple[str, str, list[str]]] = {}
    manifest_root = root.find("opf:manifest", _OPF_NS)
    if manifest_root is not None:
        for item in manifest_root.findall("opf:item", _OPF_NS):
            item_id = item.get("id")
            href = item.get("href")
            media_type = (item.get("media-type") or "").strip()
            properties = (item.get("properties") or "").split()
            if item_id and href:
                manifest[item_id] = (href, media_type, properties)

    spine_ids: list[str] = []
    spine_root = root.find("opf:spine", _OPF_NS)
    if spine_root is not None:
        for itemref in spine_root.findall("opf:itemref", _OPF_NS):
            idref = itemref.get("idref")
            linear = (itemref.get("linear") or "yes").lower()
            if idref and linear != "no":
                spine_ids.append(idref)

    return title, manifest, spine_ids


def _iter_spine_documents(
    path: Path,
) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    """Read spine XHTML only, skipping manifest images with broken hrefs."""
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        archive_names = set(archive.namelist())
        try:
            container = archive.read("META-INF/container.xml")
        except KeyError as exc:
            raise ValueError(f"Invalid EPUB (missing container.xml): {path}") from exc

        container_root = ET.fromstring(container)
        opf_path = None
        for rootfile in container_root.findall(".//c:rootfile", _CONTAINER_NS):
            if rootfile.get("media-type") == "application/oebps-package+xml":
                opf_path = rootfile.get("full-path")
                break
        if not opf_path:
            raise ValueError(f"Invalid EPUB (missing package document): {path}")

        opf_member = _resolve_epub_member("", opf_path, archive_names)
        if opf_member is None:
            opf_member = PurePosixPath(opf_path).as_posix()
        if opf_member not in archive_names:
            raise ValueError(f"Invalid EPUB (package document missing): {opf_path}")

        title, manifest, spine_ids = _parse_opf(archive.read(opf_member))
        if not spine_ids:
            spine_ids = [
                item_id
                for item_id, (_href, media_type, _props) in manifest.items()
                if media_type in _DOCUMENT_MEDIA_TYPES
            ]

        documents: list[tuple[str, bytes]] = []
        for item_id in spine_ids:
            entry = manifest.get(item_id)
            if entry is None:
                warnings.append(f"Skipped missing spine item '{item_id}'.")
                continue
            href, media_type, properties = entry
            if media_type not in _DOCUMENT_MEDIA_TYPES:
                continue
            if "nav" in properties:
                continue

            member = _resolve_epub_member(opf_member, href, archive_names)
            if member is None:
                warnings.append(f"Skipped unreadable chapter '{href}'.")
                continue
            try:
                documents.append((member, archive.read(member)))
            except KeyError:
                warnings.append(f"Skipped unreadable chapter '{href}'.")
                continue

    return title, documents, warnings


def _segments_from_chapter(chapter_number: int, chapter_text: str) -> list[SourceSegment]:
    raw_lines = chapter_text.splitlines()
    chapter_segments = segments_from_markdown(raw_lines)
    if not chapter_segments:
        location = SourceLocation(chapter=chapter_number, chapter_end=chapter_number)
        return [SourceSegment(text=chapter_text, location=location, index=0)]

    segments: list[SourceSegment] = []
    for segment in chapter_segments:
        location = segment.location
        segments.append(
            SourceSegment(
                text=segment.text,
                location=SourceLocation(
                    chapter=chapter_number,
                    chapter_end=chapter_number,
                    line_start=location.line_start,
                    line_end=location.line_end,
                ),
                index=len(segments),
            )
        )
    return segments


class EpubLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".epub"}

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        try:
            title, documents, zip_warnings = _iter_spine_documents(path)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid EPUB archive: {path}") from exc

        segments: list[SourceSegment] = []
        load_warnings = list(zip_warnings)
        skipped_chapters = 0

        for chapter_number, (_member, payload) in enumerate(documents, start=1):
            try:
                html = payload.decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 - skip unreadable chapter payloads
                skipped_chapters += 1
                logger.debug("Skipping EPUB chapter %s: %s", _member, exc)
                continue

            chapter_text = _chapter_text(html)
            if not chapter_text:
                skipped_chapters += 1
                continue

            for segment in _segments_from_chapter(chapter_number, chapter_text):
                segment.index = len(segments)
                segments.append(segment)

        if skipped_chapters:
            load_warnings.append(
                f"Skipped {skipped_chapters} EPUB chapter(s) with no readable text."
            )

        if not segments:
            raise ValueError(f"No readable chapter text found in EPUB: {path}")

        content = "\n\n".join(segment.text for segment in segments)
        return LoadedSource(
            title=title or path.stem,
            text=content,
            source_type="epub",
            source_ref=str(path),
            segments=segments,
            load_warnings=load_warnings,
        )
