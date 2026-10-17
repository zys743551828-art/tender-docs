"""Bounded OOXML inspection. Never extract archive paths or follow relationships."""

from __future__ import annotations

import io
import posixpath
import re
import stat
import struct
import unicodedata
import zipfile
from pathlib import PurePosixPath
from typing import Iterable, NoReturn, cast
from urllib.parse import unquote

# UTF-8 only, DTD/entity declarations rejected before parsing; per-part and
# whole-package node/byte/depth budgets are enforced below.
from xml.etree import ElementTree as ET  # nosec B405

from packages.domain.runtime import DomainError, sha

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"
MAIN = DOCX + ".main+xml"
MAX_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_ENTRY = 32 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_NODES = 200000
MAX_PACKAGE_XML_BYTES = 24 * 1024 * 1024
MAX_PACKAGE_NODES = 300000
MAX_DEPTH = 64
WORD_NS = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}


def reject(code, message="Word容器核验未通过，不能作为已保存原件或受检稿", status=422) -> NoReturn:
    raise DomainError(code, message, status)


def filename_type(filename):
    if (
        not isinstance(filename, str)
        or len(filename) > 1000
        or any(ord(c) < 32 for c in filename)
        or "/" in filename
        or "\\" in filename
    ):
        reject("UPLOAD_FILENAME_INVALID")
    lower = filename.casefold()
    if lower.endswith(".pdf"):
        return PDF
    if lower.endswith(".docx"):
        return DOCX
    reject("DOCUMENT_FORMAT_UNSUPPORTED", "当前支持PDF与DOCX；旧DOC请导出PDF后上传", 415)


def _xml(data):
    if len(data) > MAX_XML:
        reject("WORD_XML_LIMIT")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        reject("WORD_XML_ENCODING_UNSUPPORTED")
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        reject("WORD_XML_DECLARATION_BLOCKED")
    parser = ET.XMLPullParser(events=("start", "end"))
    nodes, depth, root = 0, 0, None
    try:
        for index in range(0, len(text), 16384):
            parser.feed(text[index : index + 16384])
            for event, element in cast(Iterable[tuple[str, ET.Element]], parser.read_events()):
                if event == "start":
                    nodes += 1
                    depth += 1
                    if root is None:
                        root = element
                    if nodes > MAX_NODES or depth > MAX_DEPTH:
                        reject("WORD_XML_COMPLEXITY_LIMIT")
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError:
        reject("WORD_XML_INVALID")
    if root is None or depth:
        reject("WORD_XML_INVALID")
    return root


def _name(value):
    if not value or len(value) > 1024 or "\\" in value or "\x00" in value or ":" in value:
        reject("WORD_ARCHIVE_PATH_INVALID")
    normalized = unicodedata.normalize("NFC", value)
    components = normalized.rstrip("/").split("/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in components):
        reject("WORD_ARCHIVE_PATH_INVALID")
    return normalized


CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
XML_RELATIONS = {
    "officeDocument",
    "header",
    "footer",
    "styles",
    "stylesWithEffects",
    "numbering",
    "settings",
    "fontTable",
    "webSettings",
    "theme",
    "footnotes",
    "endnotes",
    "comments",
    "commentsExtended",
    "commentsIds",
    "people",
    "customXml",
    "customXmlProps",
    "glossaryDocument",
    "core-properties",
    "extended-properties",
    "custom-properties",
}


def _content_types(root, names):
    if root.tag != "{" + CONTENT_NS + "}Types":
        reject("WORD_CONTENT_TYPES_INVALID")
    defaults, overrides = {}, {}
    for item in root:
        media = item.attrib.get("ContentType", "")
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", media):
            reject("WORD_CONTENT_TYPES_INVALID")
        if item.tag == "{" + CONTENT_NS + "}Default":
            extension = item.attrib.get("Extension", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", extension):
                reject("WORD_CONTENT_TYPES_INVALID")
            key = extension.casefold()
            if key in defaults:
                reject("WORD_CONTENT_TYPES_CONFLICT")
            defaults[key] = media.casefold()
        elif item.tag == "{" + CONTENT_NS + "}Override":
            part = item.attrib.get("PartName", "")
            if not part.startswith("/") or any(char in part for char in "%?#"):
                reject("WORD_CONTENT_TYPES_INVALID")
            name = _name(part[1:])
            if name not in names or name.endswith("/") or name in overrides:
                reject("WORD_CONTENT_TYPES_CONFLICT")
            overrides[name] = media.casefold()
        else:
            reject("WORD_CONTENT_TYPES_INVALID")
    return {
        name: overrides.get(name, defaults.get(name.rsplit(".", 1)[-1].casefold(), ""))
        for name in names
        if name != "[Content_Types].xml" and not name.endswith("/")
    }


def _xml_type(media):
    return media.endswith("+xml") or media in {"application/xml", "text/xml"}


def inspect_docx(data):
    _zip_directory_budget(data)
    xml = {}
    total = 0
    xml_bytes = 0
    xml_nodes = 0
    names = set()
    folded = set()
    flags = set()

    def parse_part(name, content):
        nonlocal xml_bytes, xml_nodes
        xml_bytes += len(content)
        if xml_bytes > MAX_PACKAGE_XML_BYTES:
            reject("WORD_PACKAGE_XML_LIMIT")
        root = _xml(bytes(content))
        xml_nodes += sum(1 for _ in root.iter())
        if xml_nodes > MAX_PACKAGE_NODES:
            reject("WORD_PACKAGE_XML_COMPLEXITY_LIMIT")
        xml[name] = root
        return root

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        with archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_ENTRIES:
                reject("WORD_ARCHIVE_ENTRY_LIMIT")
            for info in entries:
                if info.orig_filename != info.filename:
                    reject("WORD_ARCHIVE_PATH_INVALID")
                name = _name(info.filename)
                key = name.casefold().rstrip("/")
                if key in folded:
                    reject("WORD_ARCHIVE_DUPLICATE_ENTRY")
                folded.add(key)
                names.add(name)
                if (
                    info.flag_bits & 1
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    reject("WORD_ARCHIVE_FEATURE_BLOCKED")
                total += info.file_size
                if (
                    info.file_size > MAX_ENTRY
                    or total > MAX_EXPANDED
                    or info.file_size > max(1, info.compress_size) * 200
                ):
                    reject("WORD_ARCHIVE_EXPANSION_LIMIT")
                if info.is_dir():
                    continue
                if any(
                    part in key
                    for part in ("vbaproject", "activex/", "embeddings/", "customui/", "vba/")
                ):
                    flags.add("ACTIVE_OR_EMBEDDED_CONTENT")
            manifest = (
                archive.getinfo("[Content_Types].xml") if "[Content_Types].xml" in names else None
            )
            if manifest is None:
                reject("WORD_DOCUMENT_PARTS_MISSING")
            if manifest.file_size > MAX_XML:
                reject("WORD_XML_LIMIT")
            content_types = parse_part("[Content_Types].xml", archive.read(manifest))
            part_types = _content_types(content_types, names)
            # Discovery is driven by OPC declarations, independent of ZIP suffix.
            # XML-looking undeclared/mistyped parts are also scanned, never silently trusted.
            for info in entries:
                name = info.filename
                if info.is_dir() or name == "[Content_Types].xml":
                    continue
                media = part_types.get(name, "")
                is_xml = _xml_type(media) or name.casefold().endswith((".xml", ".rels"))
                if is_xml and info.file_size > MAX_XML:
                    reject("WORD_XML_LIMIT")
                content = bytearray()
                observed = 0
                with archive.open(info) as item:
                    while block := item.read(32768):
                        if not observed and block.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
                            is_xml = True
                        observed += len(block)
                        if observed > info.file_size or observed > MAX_ENTRY:
                            reject("WORD_ARCHIVE_EXPANSION_LIMIT")
                        if is_xml:
                            if observed > MAX_XML:
                                reject("WORD_XML_LIMIT")
                            content.extend(block)
                if observed != info.file_size:
                    reject("WORD_ARCHIVE_SIZE_MISMATCH")
                if is_xml:
                    parse_part(name, content)
                    if not _xml_type(media):
                        flags.add("XML_PART_CONTENT_TYPE_MISMATCH")
                elif not media and not any(
                    part in name.casefold()
                    for part in ("vbaproject", "activex/", "embeddings/", "customui/", "vba/")
                ):
                    flags.add("UNDECLARED_PART_CONTENT_TYPE")
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        reject("WORD_ARCHIVE_INVALID")
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required <= xml.keys():
        reject("WORD_DOCUMENT_PARTS_MISSING")
    main_type = part_types.get("word/document.xml")
    if main_type not in {MAIN.casefold(), "application/vnd.ms-word.document.macroenabled.main+xml"}:
        reject("WORD_DOCUMENT_TYPE_MISMATCH")
    if main_type != MAIN.casefold():
        flags.add("MACRO_ENABLED_MAIN_TYPE")
    document = xml["word/document.xml"]
    if not any(document.tag == "{" + namespace + "}document" for namespace in WORD_NS):
        reject("WORD_DOCUMENT_ROOT_INVALID")
    root_targets = []
    paragraphs = []
    requested_fonts = set()
    features = set()
    for name, root in xml.items():
        is_relationship = (
            part_types.get(name) == REL_TYPE
            or name.casefold().endswith(".rels")
            or root.tag == "{" + REL_NS + "}Relationships"
        )
        if is_relationship:
            if name != "_rels/.rels" and not ("/_rels/" in name and name.endswith(".rels")):
                reject("WORD_RELATIONSHIP_PART_NAME_UNSUPPORTED")
            if part_types.get(name) != REL_TYPE:
                reject("WORD_RELATIONSHIP_CONTENT_TYPE_MISMATCH")
            if (
                root.tag
                != "{http://schemas.openxmlformats.org/package/2006/relationships}Relationships"
            ):
                reject("WORD_RELATIONSHIPS_INVALID")
            seen_ids = set()
            for item in root:
                if item.tag != "{" + REL_NS + "}Relationship":
                    reject("WORD_RELATIONSHIPS_INVALID")
                relation_id, target = item.attrib.get("Id"), item.attrib.get("Target", "")
                if not relation_id or relation_id in seen_ids:
                    reject("WORD_RELATIONSHIPS_INVALID")
                seen_ids.add(relation_id)
                if item.attrib.get("TargetMode", "Internal") != "Internal" or re.match(
                    r"(?:[a-z][a-z0-9+.-]*:|//)", target, re.I
                ):
                    flags.add("EXTERNAL_RELATIONSHIP")
                    continue
                base = "" if name == "_rels/.rels" else str(PurePosixPath(name).parent.parent)
                resolved = posixpath.normpath(
                    posixpath.join(base, unquote(target).split("#", 1)[0])
                )
                if resolved.startswith(("/", "../")) or "\\" in resolved:
                    reject("WORD_RELATIONSHIP_ESCAPE")
                if resolved not in names or resolved.endswith("/"):
                    reject("WORD_RELATIONSHIP_TARGET_MISSING")
                relation_type = item.attrib.get("Type", "")
                local_type = relation_type.rsplit("/", 1)[-1]
                if local_type in XML_RELATIONS:
                    if resolved not in xml or not _xml_type(part_types.get(resolved, "")):
                        reject("WORD_RELATIONSHIP_XML_TYPE_MISMATCH")
                elif local_type != "image":
                    flags.add("UNSUPPORTED_RELATIONSHIP_TYPE")
                if name == "_rels/.rels" and item.attrib.get("Type", "").endswith(
                    "/officeDocument"
                ):
                    root_targets.append(resolved)
        for item in root.iter():
            local = item.tag.rsplit("}", 1)[-1]
            if local in {"altChunk", "object", "control", "oleObject", "movie", "webPr"}:
                flags.add("ACTIVE_OR_EMBEDDED_CONTENT")
            if local == "instrText" and re.search(
                r"\b(?:DDE(?:AUTO)?|INCLUDEPICTURE|INCLUDETEXT|LINK)\b", item.text or "", re.I
            ):
                flags.add("EXTERNAL_OR_DYNAMIC_FIELD")
            if local in {
                "drawing",
                "pict",
                "tbl",
                "txbxContent",
                "footnote",
                "endnote",
                "ins",
                "del",
                "fldChar",
                "instrText",
                "sym",
                "hdr",
                "ftr",
                "hyperlink",
                "sdt",
                "smartTag",
                "contentPart",
                "vml",
                "lastRenderedPageBreak",
            }:
                features.add(local)
            if local == "fldSimple":
                instruction = " ".join(item.attrib.values())
                if re.search(
                    r"\b(?:DDE(?:AUTO)?|INCLUDEPICTURE|INCLUDETEXT|LINK)\b", instruction, re.I
                ):
                    flags.add("EXTERNAL_OR_DYNAMIC_FIELD")
                features.add("fldSimple")
            if local == "rFonts":
                for key, value in item.attrib.items():
                    if key.rsplit("}", 1)[-1] in {"ascii", "hAnsi", "eastAsia", "cs"}:
                        requested_fonts.add(value)
                        if len(value) > 128 or len(requested_fonts) > 96:
                            reject("WORD_FONT_DECLARATION_LIMIT")
    if root_targets != ["word/document.xml"]:
        reject("WORD_MAIN_RELATIONSHIP_INVALID")
    for paragraph in document.iter():
        if paragraph.tag.rsplit("}", 1)[-1] == "p":
            value = "".join(
                item.text or ""
                for item in paragraph.iter()
                if item.tag.rsplit("}", 1)[-1] in {"t", "tab", "br"}
            )
            if value:
                paragraphs.append(value)
    if sum(len(value) for value in paragraphs) > 1_000_000:
        reject("WORD_TEXT_LIMIT")
    return {
        "schema": "word-intake-inspection/1",
        "container_validated": True,
        "entry_count": len(names),
        "expanded_bytes": total,
        "xml_part_count": len(xml),
        "active_content_gaps": sorted(flags),
        "unsupported_layout_features": sorted(features),
        "requested_fonts": sorted(requested_fonts),
        "conversion_eligible": not flags,
        "text_digest": sha("\n".join(paragraphs).encode()),
        "paragraphs": paragraphs,
    }


def inspect_upload(filename, data, declared_digest, declared_size):
    media_type = filename_type(filename)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES or len(data) != declared_size:
        reject("UPLOAD_SIZE_MISMATCH")
    if sha(data) != declared_digest:
        reject("UPLOAD_DIGEST_MISMATCH")
    if media_type == PDF:
        if not data.startswith(b"%PDF-"):
            reject("INVALID_PDF")
        return {
            "media_type": PDF,
            "digest": declared_digest,
            "byte_size": len(data),
            "inspection": None,
        }
    if not data.startswith(b"PK\x03\x04"):
        reject("WORD_ARCHIVE_INVALID")
    return {
        "media_type": DOCX,
        "digest": declared_digest,
        "byte_size": len(data),
        "inspection": inspect_docx(data),
    }


def public_report(inspection):
    """Do not copy private Word paragraphs into DB logs/inspection projections."""
    return {key: value for key, value in inspection.items() if key != "paragraphs"}


def _zip_directory_budget(data):
    """Count central-directory records before ZipFile allocates ZipInfo objects."""
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or len(data) - end < 22:
        reject("WORD_ARCHIVE_INVALID")
    disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4H2LH", data, end + 4
    )
    if (
        end + 22 + comment != len(data)
        or disk
        or directory_disk
        or disk_count != count
        or count == 65535
    ):
        reject("WORD_ARCHIVE_LAYOUT_UNSUPPORTED")
    if not 1 <= count <= MAX_ENTRIES or size > 8 * 1024 * 1024:
        reject("WORD_ARCHIVE_ENTRY_LIMIT")
    if offset + size != end or offset < 4:
        reject("WORD_ARCHIVE_INVALID")
    cursor, observed = offset, 0
    while cursor < end:
        if cursor + 46 > end or data[cursor : cursor + 4] != b"PK\x01\x02":
            reject("WORD_ARCHIVE_INVALID")
        name, extra, own_comment = struct.unpack_from("<3H", data, cursor + 28)
        cursor += 46 + name + extra + own_comment
        observed += 1
        if cursor > end or observed > MAX_ENTRIES:
            reject("WORD_ARCHIVE_ENTRY_LIMIT")
    if observed != count or cursor != end:
        reject("WORD_ARCHIVE_ENTRY_COUNT_MISMATCH")
