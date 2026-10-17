"""Deterministic OOXML for the strict text tree and its two frozen templates."""

import io
import zipfile
from pathlib import Path

# Internal generated OOXML only; no uploaded XML is parsed in this module.
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring import snapshot_structure
from packages.authoring.build import W, assemble_docx, element
from packages.authoring.structure_model import ordered
from packages.domain.runtime import DomainError, sha

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WRITER_DIGEST = sha(Path(__file__).read_bytes())


def fromstring(data):
    # Only bytes returned by our own assemble_docx enter this parser.
    return ET.fromstring(data)  # nosec B314


def paragraph(text, style=None):
    p = ET.Element("{" + W + "}p")
    if style:
        element(element(p, "pPr"), "pStyle", val=style)
    element(element(p, "r"), "t", text)
    return p


def assemble(snapshot):
    extension = snapshot_structure.checked(snapshot["authoring_structure"])
    nodes = ordered(extension["nodes"])
    if len(nodes) != len(snapshot["sections"]):
        raise DomainError("AUTHORING_STRUCTURE_CORRUPT", "正文与目录数量不一致", 409)
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    from packages.authoring.templates import checked_config

    checked_config("layout", config)
    source = assemble_docx(snapshot)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    document = fromstring(files["word/document.xml"])
    body = document.find("{" + W + "}body")
    paragraphs = list(body.findall("{" + W + "}p"))
    names = []
    paths: dict[str, list[int]] = {}
    position = 0
    for section, node in zip(snapshot["sections"], nodes, strict=True):
        parent = node["parent_id"]
        path = paths.get(parent, []) + [node["ordinal"] + 1]
        paths[node["section_id"]] = path
        depth = len(path)
        title = section["attrs"]["title"]
        if config["chapter_numbering"] == "decimal":
            title = ".".join(map(str, path)) + " " + title
        names.append((depth, title))
        heading = paragraphs[position]
        heading.find("{" + W + "}pPr/{" + W + "}pStyle").set(
            "{" + W + "}val", "Heading" + str(depth)
        )
        heading.find("{" + W + "}r/{" + W + "}t").text = title
        position += 1

        index = [position]

        def headings(value, base_depth=depth, index=index):
            if value["type"] == "heading":
                level = base_depth + value["attrs"]["level"]
                if level > 6:
                    raise DomainError(
                        "AUTHORING_HEADING_DEPTH_UNSUPPORTED",
                        "章内标题与目录层级合计超过六级，请调整后生成",
                        422,
                    )
                paragraphs[index[0]].find("{" + W + "}pPr/{" + W + "}pStyle").set(
                    "{" + W + "}val", "Heading" + str(level)
                )
            if value["type"] in {"bulletList", "orderedList", "listItem"}:
                for child in value["content"]:
                    headings(child)
            else:
                index[0] += 1

        # Local index avoids matching chapter names against arbitrary body text.
        for value in section["content"]:
            headings(value)
        position = index[0]
    front = []
    if config["cover_enabled"]:
        front.append(paragraph(config["cover_title"] or snapshot.get("title", "投标正文")))
        page = paragraph("")
        element(element(page, "r"), "br", type="page")
        front.append(page)
    if config["toc_enabled"]:
        front.append(paragraph("目录"))
        for depth, title in names:
            entry = paragraph(title)
            element(element(entry, "pPr"), "ind", left=(depth - 1) * 360)
            front.append(entry)
        page = paragraph("")
        element(element(page, "r"), "br", type="page")
        front.append(page)
    for i, entry in enumerate(front):
        body.insert(i, entry)
    sect = body.find("{" + W + "}sectPr")
    margin = sect.find("{" + W + "}pgMar")
    for side in ["top", "right", "bottom", "left"]:
        margin.set("{" + W + "}" + side, str(config["margin_" + side + "_twips"]))
    styles = fromstring(files["word/styles.xml"])
    for font in styles.iter("{" + W + "}rFonts"):
        for name in ["ascii", "hAnsi", "eastAsia"]:
            font.set("{" + W + "}" + name, config["font"])
    defaults = styles.find("{" + W + "}docDefaults/{" + W + "}rPrDefault/{" + W + "}rPr")
    defaults.find("{" + W + "}sz").set("{" + W + "}val", str(config["font_size_halfpoints"]))
    for size in styles.iter("{" + W + "}sz"):
        size.set("{" + W + "}val", str(config["font_size_halfpoints"]))
    pp = element(element(styles.find("{" + W + "}docDefaults"), "pPrDefault"), "pPr")
    element(pp, "spacing", after=config["paragraph_after_twips"])
    relationships = fromstring(files["word/_rels/document.xml.rels"])
    types = fromstring(files["[Content_Types].xml"])
    reference_count = 0
    for name, text in [
        ("header", config["header_text"]),
        ("footer", config["footer_text"]),
    ]:
        if not text:
            continue
        part = ET.Element("{" + W + "}" + ("hdr" if name == "header" else "ftr"))
        p = paragraph(text)
        part.append(p)
        key = "authoring_" + name
        reference = element(sect, name + "Reference", type="default")
        reference.set("{" + R + "}id", key)
        sect.remove(reference)
        sect.insert(reference_count, reference)
        reference_count += 1
        ET.SubElement(
            relationships,
            "{" + REL + "}Relationship",
            {"Id": key, "Type": R + "/" + name, "Target": name + ".xml"},
        )
        ET.SubElement(
            types,
            "{" + CT + "}Override",
            {
                "PartName": "/word/" + name + ".xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml."
                + name
                + "+xml",
            },
        )
        files["word/" + name + ".xml"] = ET.tostring(part, encoding="utf-8", xml_declaration=True)
    for key, tree in [
        ("word/document.xml", document),
        ("word/styles.xml", styles),
        ("word/_rels/document.xml.rels", relationships),
        ("[Content_Types].xml", types),
    ]:
        files[key] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for key, data in sorted(files.items()):
            info = zipfile.ZipInfo(key, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()
