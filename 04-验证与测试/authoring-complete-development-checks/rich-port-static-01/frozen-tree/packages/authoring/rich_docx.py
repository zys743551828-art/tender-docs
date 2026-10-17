"""Deterministic native-table/embedded-PNG producer; conversion needs a new review.

This producer is not accepted by the PAGE-only v2 sandbox. Never pass its package
through the external Word lane as a means of bypassing generated RICH admission.
"""

from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring import (
    build,
    generated_docx,
    rich_content,
    rich_images,
    rich_ooxml,
    structured_docx,
)
from packages.authoring.build import W, element
from packages.authoring.structure_model import ordered
from packages.domain.runtime import DomainError, sha

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
PROTOCOL = "authoring-generated-rich/3"


def identity():
    from packages.authoring import page_navigation

    return {
        "protocol": PROTOCOL,
        "text_generator": generated_docx.generator_identity(),
        "sources": {
            Path(str(module.__file__)).name: sha(Path(str(module.__file__)).read_bytes())
            for module in [rich_content, rich_images, rich_ooxml, page_navigation]
        },
        "producer_digest": sha(Path(__file__).read_bytes()),
        "schema_digest": rich_content.SCHEMA_DIGEST,
    }


def navigation_entries(snapshot, pagination=None):
    from packages.authoring.page_navigation import anchor_for

    nodes = ordered(snapshot["authoring_structure"]["nodes"])
    keys = [node["section_id"] for node in nodes]
    pagination = pagination or {
        "mode": "continuous",
        "toc_start_pages": {key: None for key in keys},
    }
    if (
        not isinstance(pagination, dict)
        or set(pagination) != {"mode", "toc_start_pages"}
        or pagination["mode"] not in {"continuous", "chapter_start_new_page"}
        or not isinstance(pagination["toc_start_pages"], dict)
        or set(pagination["toc_start_pages"]) != set(keys)
        or any(
            page is not None and (type(page) is not int or not 1 <= page <= 2000)
            for page in pagination["toc_start_pages"].values()
        )
    ):
        raise DomainError(
            "AUTHORING_PAGINATION_INVALID", "章页模式和目录数字必须精确覆盖冻结章节", 422
        )
    extension = snapshot["authoring_structure"]
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    paths: dict[str | None, list[int]] = {}
    entries = []
    for node, section in zip(nodes, snapshot["sections"], strict=True):
        path = paths.get(node["parent_id"], []) + [node["ordinal"] + 1]
        paths[node["section_id"]] = path
        title = section["attrs"]["title"]
        if config["chapter_numbering"] == "decimal":
            title = ".".join(map(str, path)) + " " + title
        entries.append(
            {
                "node_id": node["section_id"],
                "parent_id": node["parent_id"],
                "anchor": anchor_for(node["section_id"]),
                "title": title,
                "expected_start_page": pagination["toc_start_pages"][node["section_id"]],
            }
        )
    return entries


def decorate_navigation(document, projected, config, pagination):
    """Use exact paragraph positions from our base producer, never title searching."""
    entries = navigation_entries(projected, pagination)
    body = document.find("{" + W + "}body")
    paragraphs = body.findall("{" + W + "}p")
    front = (2 if config["cover_enabled"] else 0) + (
        len(entries) + 2 if config["toc_enabled"] else 0
    )

    def count(values):
        return sum(
            count(value["content"])
            if value["type"] in {"bulletList", "orderedList", "listItem"}
            else 1
            for value in values
        )

    if len(paragraphs) != front + sum(
        1 + count(section["content"]) for section in projected["sections"]
    ):
        raise DomainError(
            "AUTHORING_NAVIGATION_STRUCTURE_CHANGED", "固定章节/目录段落定位与生产器不一致", 409
        )
    index = front
    mode = pagination["mode"] if pagination else "continuous"
    for number, (entry, section) in enumerate(
        zip(entries, projected["sections"], strict=True), start=1
    ):
        heading = paragraphs[index]
        if "".join(t.text or "" for t in heading.iter("{" + W + "}t")) != entry["title"]:
            raise DomainError(
                "AUTHORING_NAVIGATION_STRUCTURE_CHANGED", "稳定章节定位的可见标题不一致", 409
            )
        properties = heading.find("{" + W + "}pPr")
        if mode == "chapter_start_new_page":
            element(properties, "pageBreakBefore")
        bookmark = ET.Element(
            "{" + W + "}bookmarkStart",
            {"{" + W + "}id": str(number), "{" + W + "}name": entry["anchor"]},
        )
        heading.insert(1, bookmark)
        element(heading, "bookmarkEnd", id=number)
        if config["toc_enabled"]:
            toc = paragraphs[(2 if config["cover_enabled"] else 0) + number]
            for child in list(toc):
                if child.tag != "{" + W + "}pPr":
                    toc.remove(child)
            properties = toc.find("{" + W + "}pPr")
            if properties is None:
                properties = ET.Element("{" + W + "}pPr")
                toc.insert(0, properties)
            tabs = element(properties, "tabs")
            element(
                tabs,
                "tab",
                val="right",
                leader="dot",
                pos=11906 - config["margin_left_twips"] - config["margin_right_twips"] - 60,
            )
            link = element(toc, "hyperlink", anchor=entry["anchor"])
            element(element(link, "r"), "t", entry["title"])
            run = element(toc, "r")
            element(run, "tab")
            element(
                run,
                "t",
                str(entry["expected_start_page"])
                if entry["expected_start_page"] is not None
                else "—",
            )
        index += 1 + count(section["content"])
    return entries


def node(parent, namespace, localname, **attrs):
    return ET.SubElement(
        parent, "{" + namespace + "}" + localname, {k: str(v) for k, v in attrs.items()}
    )


def picture(attrs, relationship, sequence):
    paragraph = ET.Element("{" + W + "}p")
    drawing = element(element(paragraph, "r"), "drawing")
    inline = node(drawing, WP, "inline", distT=0, distB=0, distL=0, distR=0)
    width, height = attrs["width_twips"] * 635, attrs["height_twips"] * 635
    node(inline, WP, "extent", cx=width, cy=height)
    node(inline, WP, "effectExtent", l=0, t=0, r=0, b=0)
    node(inline, WP, "docPr", id=sequence, name="Image " + str(sequence), descr=attrs["alt"])
    locks = node(inline, WP, "cNvGraphicFramePr")
    node(locks, A, "graphicFrameLocks", noChangeAspect=1)
    graphic = node(inline, A, "graphic")
    data = node(graphic, A, "graphicData", uri=PIC)
    image = node(data, PIC, "pic")
    props = node(image, PIC, "nvPicPr")
    node(
        props, PIC, "cNvPr", id=sequence, name="Normalized PNG " + str(sequence), descr=attrs["alt"]
    )
    node(props, PIC, "cNvPicPr")
    fill = node(image, PIC, "blipFill")
    blip = node(fill, A, "blip")
    blip.set("{" + R + "}embed", relationship)
    node(node(fill, A, "stretch"), A, "fillRect")
    props = node(image, PIC, "spPr")
    transform = node(props, A, "xfrm")
    node(transform, A, "off", x=0, y=0)
    node(transform, A, "ext", cx=width, cy=height)
    node(node(props, A, "prstGeom", prst="rect"), A, "avLst")
    return paragraph


def assemble(snapshot, image_materials, *, pagination=None):
    """image_materials is a trusted exact allowlist, never a client object path.

    Each entry contains asset/version identity, frozen normalization proof and
    actual bytes already fetched outside a DB transaction. Return actual OOXML.
    """
    try:
        if (
            len(json.dumps(snapshot, ensure_ascii=False).encode())
            > generated_docx.MAX_SNAPSHOT_BYTES
        ):
            raise DomainError("AUTHORING_GENERATED_SCOPE_LIMIT", "完整表图快照超过16MiB", 422)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DomainError(
            "AUTHORING_RICH_SNAPSHOT_INVALID", "表图快照不是可固定的JSON", 422
        ) from None
    if (
        not isinstance(image_materials, list)
        or len(image_materials) > 500
        or any(
            not isinstance(m, dict) or not isinstance(m.get("bytes"), bytes)
            for m in image_materials
        )
        or sum(len(m["bytes"]) for m in image_materials) > 64 * 1024 * 1024
    ):
        raise DomainError("AUTHORING_IMAGE_MANIFEST_INVALID", "规范图片总量超过64MiB或500项", 422)
    extension = snapshot["authoring_structure"]
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    available = 11906 - config["margin_left_twips"] - config["margin_right_twips"]
    available_height = 16838 - config["margin_top_twips"] - config["margin_bottom_twips"]
    projected = deepcopy(snapshot)
    replacements = {}
    required = set()
    materials = {}
    decoded_pixels, decoded_digests = 0, set()
    for material in image_materials:
        key = (material["asset_id"], material["asset_revision"], material["asset_version_id"])
        if key in materials:
            raise DomainError("AUTHORING_IMAGE_MANIFEST_INVALID", "图片版本清单重复", 409)
        rich_images.verify_normalized(material["bytes"], material["normalization_proof"])
        proof = material["normalization_proof"]
        if proof["output_digest"] not in decoded_digests:
            decoded_digests.add(proof["output_digest"])
            decoded_pixels += proof["width_px"] * proof["height_px"]
            if decoded_pixels > 32_000_000:
                raise DomainError(
                    "AUTHORING_IMAGE_PIXEL_BUDGET",
                    "全稿不同图片累计超过3200万像素，请压缩图片后再生成",
                    422,
                )
        materials[key] = material
    for section in projected["sections"]:
        rich_content.validate_content(
            section,
            available_width_twips=available,
            available_height_twips=available_height,
        )
        for index, block in enumerate(section["content"]):
            if block["type"] not in {"table", "image"}:
                continue
            marker = "AUTHORING_RICH_" + sha({"section": section["attrs"], "block": block})
            if marker in replacements:
                raise DomainError(
                    "AUTHORING_RICH_MARKER_COLLISION", "内容定位重复，请检查节点ID", 409
                )
            replacements[marker] = block
            if block["type"] == "image":
                attrs = block["attrs"]
                required.add(
                    (attrs["asset_id"], attrs["asset_revision"], attrs["asset_version_id"])
                )
            section["content"][index] = {
                "type": "paragraph",
                "attrs": {"id": block["attrs"]["id"]},
                "content": [{"type": "text", "text": marker}],
            }
    if required != set(materials):
        raise DomainError(
            "AUTHORING_IMAGE_MANIFEST_INVALID", "图片清单必须精确覆盖正文使用版本", 409
        )
    placements = sum(
        materials[
            (
                block["attrs"]["asset_id"],
                block["attrs"]["asset_revision"],
                block["attrs"]["asset_version_id"],
            )
        ]["normalization_proof"]["width_px"]
        * materials[
            (
                block["attrs"]["asset_id"],
                block["attrs"]["asset_revision"],
                block["attrs"]["asset_version_id"],
            )
        ]["normalization_proof"]["height_px"]
        for block in replacements.values()
        if block["type"] == "image"
    )
    if placements > 32_000_000:
        raise DomainError(
            "AUTHORING_IMAGE_PIXEL_BUDGET",
            "全稿图片按使用次数累计超过3200万像素，请压缩后生成",
            422,
        )
    source = generated_docx.assemble(projected)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    document = structured_docx.fromstring(files["word/document.xml"])
    decorate_navigation(document, projected, config, pagination)
    body = document.find("{" + W + "}body")
    numbering = structured_docx.fromstring(files["word/numbering.xml"])
    relationships = structured_docx.fromstring(files["word/_rels/document.xml.rels"])
    types = structured_docx.fromstring(files["[Content_Types].xml"])
    sequence = 0
    emitted = set()

    def blocks(parent, values):
        mini = {
            "sections": [
                {
                    "type": "doc",
                    "attrs": {"title": "Cell", "ordinal": 0, "rule_ids": [], "source_refs": []},
                    "content": values,
                }
            ]
        }
        with zipfile.ZipFile(io.BytesIO(build.assemble_docx(mini))) as package:
            local = structured_docx.fromstring(package.read("word/document.xml"))
            local_numbering = structured_docx.fromstring(package.read("word/numbering.xml"))
        mapping = {}
        existing = [int(n.get("{" + W + "}numId")) for n in numbering.findall("{" + W + "}num")]
        next_id = max(existing, default=0) + 1
        for old in local_numbering.findall("{" + W + "}num"):
            mapping[old.get("{" + W + "}numId")] = str(next_id)
            old.set("{" + W + "}numId", str(next_id))
            numbering.append(old)
            next_id += 1
        for p in local.find("{" + W + "}body").findall("{" + W + "}p")[1:]:
            for number in p.findall(".//{" + W + "}numId"):
                number.set("{" + W + "}val", mapping[number.get("{" + W + "}val")])
            parent.append(p)

    for paragraph in list(body):
        if paragraph.tag != "{" + W + "}p":
            continue
        marker = "".join(t.text or "" for t in paragraph.iter("{" + W + "}t"))
        if marker not in replacements:
            continue
        if marker in emitted:
            raise DomainError("AUTHORING_RICH_MARKER_COLLISION", "内容定位重复", 409)
        emitted.add(marker)
        block = replacements[marker]
        position = list(body).index(paragraph)
        if block["type"] == "table":
            actual = rich_ooxml.write_table(block, blocks, available_width_twips=available)
        else:
            sequence += 1
            attrs = block["attrs"]
            material = materials[
                (attrs["asset_id"], attrs["asset_revision"], attrs["asset_version_id"])
            ]
            digest = material["normalization_proof"]["output_digest"]
            relation = "authoring_image_" + digest
            part = "word/media/" + digest + ".png"
            if part not in files:
                files[part] = material["bytes"]
                node(
                    relationships,
                    REL,
                    "Relationship",
                    Id=relation,
                    Type=R + "/image",
                    Target="media/" + digest + ".png",
                )
            actual = picture(attrs, relation, sequence)
        body.remove(paragraph)
        body.insert(position, actual)
        if block["attrs"]["caption"]:
            body.insert(position + 1, structured_docx.paragraph(block["attrs"]["caption"]))
    if emitted != set(replacements):
        raise DomainError(
            "AUTHORING_RICH_MARKER_MISSING", "未完整生成表图，不能保存为空白产物", 409
        )
    if materials:
        node(types, CT, "Default", Extension="png", ContentType="image/png")
    files["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    files["word/numbering.xml"] = ET.tostring(numbering, encoding="utf-8", xml_declaration=True)
    files["word/_rels/document.xml.rels"] = structured_docx.package_xml(relationships, REL)
    files["[Content_Types].xml"] = structured_docx.package_xml(types, CT)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    result = output.getvalue()
    if len(result) > generated_docx.MAX_DOCX_BYTES:
        raise DomainError("AUTHORING_GENERATED_SCOPE_LIMIT", "表图Word超过64MiB范围", 422)
    return result
