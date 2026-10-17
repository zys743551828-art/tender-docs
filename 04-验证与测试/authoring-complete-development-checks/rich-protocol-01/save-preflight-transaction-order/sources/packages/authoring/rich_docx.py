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
from packages.domain.runtime import DomainError, sha

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
PROTOCOL = "authoring-generated-rich/3"


def identity():
    return {
        "protocol": PROTOCOL,
        "text_generator": generated_docx.generator_identity(),
        "sources": {
            Path(str(module.__file__)).name: sha(Path(str(module.__file__)).read_bytes())
            for module in [rich_content, rich_images, rich_ooxml]
        },
        "producer_digest": sha(Path(__file__).read_bytes()),
        "schema_digest": rich_content.SCHEMA_DIGEST,
    }


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


def assemble(snapshot, image_materials):
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
    for material in image_materials:
        key = (material["asset_id"], material["asset_revision"], material["asset_version_id"])
        if key in materials:
            raise DomainError("AUTHORING_IMAGE_MANIFEST_INVALID", "图片版本清单重复", 409)
        rich_images.verify_normalized(material["bytes"], material["normalization_proof"])
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
    source = generated_docx.assemble(projected)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    document = structured_docx.fromstring(files["word/document.xml"])
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
