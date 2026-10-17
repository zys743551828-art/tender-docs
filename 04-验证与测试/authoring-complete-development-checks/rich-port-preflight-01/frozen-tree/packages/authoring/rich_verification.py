"""RICH admission: exact reassembly AND bounded new-subtree grammar.

After checking table/drawing/PNG closure, the remaining package is checked by the
unchanged PAGE-only validator. External Word admission is never relaxed.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring import generated_verification as legacy
from packages.authoring import rich_docx, rich_images, structured_docx
from packages.authoring.build import W
from packages.domain.runtime import sha

TABLE = {
    "bookmarkStart": {"id", "name"},
    "bookmarkEnd": {"id"},
    "hyperlink": {"anchor"},
    "pageBreakBefore": set(),
    "tabs": set(),
    "tab": {"val", "leader", "pos"},
    "tbl": set(),
    "tblPr": set(),
    "tblW": {"w", "type"},
    "tblLayout": {"type"},
    "tblBorders": set(),
    "top": {"val", "sz", "color"},
    "left": {"val", "sz", "color"},
    "bottom": {"val", "sz", "color"},
    "right": {"val", "sz", "color"},
    "insideH": {"val", "sz", "color"},
    "insideV": {"val", "sz", "color"},
    "tblGrid": set(),
    "gridCol": {"w"},
    "tr": set(),
    "tc": set(),
    "tcPr": set(),
    "tcW": {"w", "type"},
    "gridSpan": {"val"},
    "vMerge": {"val"},
    "shd": {"val", "fill"},
    "drawing": set(),
}
DRAWING = {
    rich_docx.WP: {
        "inline": {"distT", "distB", "distL", "distR"},
        "extent": {"cx", "cy"},
        "effectExtent": {"l", "t", "r", "b"},
        "docPr": {"id", "name", "descr"},
        "cNvGraphicFramePr": set(),
    },
    rich_docx.A: {
        "graphicFrameLocks": {"noChangeAspect"},
        "graphic": set(),
        "graphicData": {"uri"},
        "blip": {"{" + rich_docx.R + "}embed"},
        "stretch": set(),
        "fillRect": set(),
        "xfrm": set(),
        "off": {"x", "y"},
        "ext": {"cx", "cy"},
        "prstGeom": {"prst"},
        "avLst": set(),
    },
    rich_docx.PIC: {
        "pic": set(),
        "nvPicPr": set(),
        "cNvPr": {"id", "name", "descr"},
        "cNvPicPr": set(),
        "blipFill": set(),
        "spPr": set(),
    },
}


def xml(raw):
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024 or b"\0" in raw:
        legacy.blocked("表图XML超限或不是UTF-8")
    try:
        text = raw.decode("utf-8", errors="strict")
        if (
            "<!" in text
            or text.count("<?") > 1
            or not re.match(r"^<\?xml\s", text)
            or re.search(r"encoding\s*=\s*['\"](?!utf-8['\"])", text, re.I)
        ):
            legacy.blocked("表图XML不允许DTD、实体、处理指令或其他编码")
        return ET.fromstring(raw)  # nosec B314: bounded UTF-8, no DTD/entities/PI.
    except (UnicodeError, ET.ParseError):
        legacy.blocked("表图XML不完整")


def verify_container(source, image_materials, *, page_number):
    expected_media = {}
    for item in image_materials:
        rich_images.verify_normalized(item["bytes"], item["normalization_proof"])
        digest = item["normalization_proof"]["output_digest"]
        expected_media["word/media/" + digest + ".png"] = item["bytes"]
    if not isinstance(source, bytes) or len(source) > 64 * 1024 * 1024:
        legacy.blocked("表图Word超过64MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            entries = archive.infolist()
            names = [i.filename for i in entries]
            allowed = legacy.BASE_PARTS | legacy.OPTIONAL_PARTS | set(expected_media)
            if (
                len(names) != len(set(names))
                or not legacy.BASE_PARTS <= set(names)
                or not set(names) <= allowed
                or not set(expected_media) <= set(names)
            ):
                legacy.blocked("表图部件清单与规范图manifest不符")
            if sum(i.file_size for i in entries) > 96 * 1024 * 1024:
                legacy.blocked("表图ZIP展开总量超过96MiB")
            files = {}
            for item in entries:
                limit = 16 * 1024 * 1024 if item.filename in expected_media else 8 * 1024 * 1024
                if (
                    item.flag_bits & 1
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.compress_type != zipfile.ZIP_DEFLATED
                    or item.file_size > limit
                    or item.file_size > max(item.compress_size, 1) * 200
                ):
                    legacy.blocked("表图ZIP资源或容器特性超出范围")
                files[item.filename] = archive.read(item)
    except (zipfile.BadZipFile, OSError, RuntimeError):
        legacy.blocked("表图Word不是可读容器")
    for name, data in expected_media.items():
        if files.pop(name) != data:
            legacy.blocked("嵌入图片并非冻结规范字节")
    original = xml(files["word/document.xml"])
    if original.tag != "{" + W + "}document" or original.attrib:
        legacy.blocked("表图主部件根不符合固定类型")
    for count, item in enumerate(original.iter(), start=1):
        if count > 200000:
            legacy.blocked("表图XML节点数量超出范围")
        if item.tag.startswith("{" + W + "}"):
            local = item.tag.split("}", 1)[1]
            attrs = TABLE.get(local, legacy.ATTRS.get(local))
            if (
                attrs is None
                or local == "fldSimple"
                or not set(item.attrib) <= {legacy.qualified(a) for a in attrs}
            ):
                legacy.blocked("表图主部件含动态域或未知元素/属性")
        else:
            accepted = False
            for namespace, grammar in DRAWING.items():
                if item.tag.startswith("{" + namespace + "}"):
                    attrs = grammar.get(item.tag.split("}", 1)[1])
                    accepted = attrs is not None and set(item.attrib) <= attrs
                    break
            if not accepted:
                legacy.blocked("图片drawing元素或属性不在固定白名单")
        if item.tag != "{" + W + "}t" and item.text and item.text.strip():
            legacy.blocked("表图文字不得藏在非文字节点")
        if item.tail and item.tail.strip():
            legacy.blocked("表图不得含隐藏尾随文本")
    bookmarks, ends, links = {}, [], []
    for item in original.iter("{" + W + "}bookmarkStart"):
        identity, name = item.get("{" + W + "}id"), item.get("{" + W + "}name", "")
        if (
            identity in bookmarks
            or not isinstance(identity, str)
            or not identity.isdecimal()
            or not 1 <= int(identity) <= 500
            or not re.fullmatch(r"sec_[0-9a-f]{32}", name)
            or list(item)
        ):
            legacy.blocked("章节书签必须是唯一稳定的内部UUID锚点")
        bookmarks[identity] = name
    ends = [item.get("{" + W + "}id") for item in original.iter("{" + W + "}bookmarkEnd")]
    links = [item.get("{" + W + "}anchor") for item in original.iter("{" + W + "}hyperlink")]
    if (
        len(set(bookmarks.values())) != len(bookmarks)
        or len(ends) != len(set(ends))
        or set(ends) != set(bookmarks)
        or not set(links) <= set(bookmarks.values())
    ):
        legacy.blocked("内部目录链接或章节书签配对不完整")
    sanitized = deepcopy(original)
    for parent in list(sanitized.iter()):
        for child in list(parent):
            if child.tag in {"{" + W + "}tbl", "{" + W + "}drawing"}:
                position = list(parent).index(child)
                parent.remove(child)
                parent.insert(
                    position,
                    ET.Element("{" + W + "}p" if child.tag.endswith("}tbl") else "{" + W + "}t"),
                )
            elif child.tag == "{" + W + "}hyperlink":
                position = list(parent).index(child)
                parent.remove(child)
                for offset, run in enumerate(list(child)):
                    parent.insert(position + offset, run)
            elif child.tag in {
                "{" + W + "}" + name
                for name in ["bookmarkStart", "bookmarkEnd", "pageBreakBefore", "tabs", "tab"]
            }:
                parent.remove(child)
    files["word/document.xml"] = ET.tostring(sanitized, encoding="utf-8", xml_declaration=True)
    relationships = xml(files["word/_rels/document.xml.rels"])
    expected_rels = {
        "authoring_image_" + name.rsplit("/", 1)[1][:-4]: "media/" + name.rsplit("/", 1)[1]
        for name in expected_media
    }
    found = set()
    for item in list(relationships):
        identity = item.get("Id")
        if identity in expected_rels:
            if (
                identity in found
                or item.tag != "{" + rich_docx.REL + "}Relationship"
                or item.attrib
                != {
                    "Id": identity,
                    "Type": rich_docx.R + "/image",
                    "Target": expected_rels[identity],
                }
            ):
                legacy.blocked("图片关系必须精确指向包内规范PNG")
            found.add(identity)
            relationships.remove(item)
    if found != set(expected_rels):
        legacy.blocked("图片关系清单不完整")
    expected_embeds = set(expected_rels)
    if {
        item.get("{" + rich_docx.R + "}embed")
        for item in original.iter("{" + rich_docx.A + "}blip")
    } != expected_embeds:
        legacy.blocked("实际drawing与冻结图片关系不符")
    files["word/_rels/document.xml.rels"] = structured_docx.package_xml(
        relationships, rich_docx.REL
    )
    types = xml(files["[Content_Types].xml"])
    png = [item for item in types if item.get("Extension") == "png"]
    if len(png) != int(bool(expected_media)) or any(
        item.tag != "{" + rich_docx.CT + "}Default"
        or item.attrib != {"Extension": "png", "ContentType": "image/png"}
        for item in png
    ):
        legacy.blocked("规范图内容类型不完整")
    for item in png:
        types.remove(item)
    files["[Content_Types].xml"] = structured_docx.package_xml(types, rich_docx.CT)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    roots = legacy.verify_container(output.getvalue(), page_number=page_number)
    roots["word/document.xml"] = original
    return roots


def manifest(source, snapshot, materials, *, pagination=None):
    entries = rich_docx.navigation_entries(snapshot, pagination)
    return {
        "protocol": rich_docx.PROTOCOL,
        "artifact_kind": "bid_body",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "structure_digest": snapshot["authoring_structure"]["digest"],
        "sections_digest": sha(snapshot["sections"]),
        "generator_digest": sha(rich_docx.identity()),
        "image_materials_digest": sha(
            [{k: v for k, v in item.items() if k != "bytes"} for item in materials]
        ),
        "source_docx_digest": sha(source),
        "navigation_entries_digest": sha(entries),
        "pagination": {
            "mode": pagination["mode"] if pagination else "continuous",
            "toc_start_pages": {item["node_id"]: item["expected_start_page"] for item in entries},
        },
    }


def verify(source, snapshot, materials, producer_manifest, generator_digest, *, pagination=None):
    if not isinstance(source, bytes) or len(source) > 64 * 1024 * 1024:
        legacy.blocked("表图Word字节类型或大小超过范围")
    if generator_digest != sha(rich_docx.identity()) or producer_manifest != manifest(
        source, snapshot, materials, pagination=pagination
    ):
        legacy.blocked("RICH生产器或完整输入身份已变化")
    if source != rich_docx.assemble(snapshot, materials, pagination=pagination):
        legacy.blocked("RICH字节不是冻结快照及精确资产的完整重生成产物")
    extension = snapshot["authoring_structure"]
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    roots = verify_container(source, materials, page_number=config["page_number"])
    return {
        "protocol": rich_docx.PROTOCOL,
        "generator_digest": generator_digest,
        "validator_digest": sha(Path(__file__).read_bytes()),
        "source_docx_digest": sha(source),
        "producer_manifest_digest": sha(producer_manifest),
        "parts": sorted(
            set(roots)
            | {
                "word/media/" + m["normalization_proof"]["output_digest"] + ".png"
                for m in materials
            }
        ),
        "native_table_count": len(list(roots["word/document.xml"].iter("{" + W + "}tbl"))),
        "image_drawing_count": len(list(roots["word/document.xml"].iter("{" + W + "}drawing"))),
    }
