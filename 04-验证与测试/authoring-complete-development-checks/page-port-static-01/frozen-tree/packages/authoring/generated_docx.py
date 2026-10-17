"""Deterministic trusted-text producer v2. PAGE is the sole generated dynamic field."""

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring import build, content, snapshot_structure, structure_schema, structured_docx
from packages.domain.runtime import DomainError, sha

W = build.W
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PROTOCOL = "authoring-generated-text/2"
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_DOCX_BYTES = 64 * 1024 * 1024


def generator_identity():
    from packages.authoring import templates

    modules = [
        Path(__file__),
        Path(build.__file__),
        Path(content.__file__),
        Path(structured_docx.__file__),
        Path(snapshot_structure.__file__),
        Path(templates.__file__),
    ]
    return {
        "protocol": PROTOCOL,
        "sources": {p.name: sha(p.read_bytes()) for p in modules},
        "content_schema_digest": content.SCHEMA_DIGEST,
        "structure_schema_digest": structure_schema.SCHEMA_DIGEST,
    }


def assemble(snapshot):
    import json

    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise DomainError("AUTHORING_GENERATED_SCOPE_LIMIT", "生成正文完整输入超过16MiB范围", 422)
    extension = snapshot_structure.checked(snapshot["authoring_structure"])
    options = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    raw = structured_docx.assemble(snapshot)
    if not options["page_number"]:
        return raw
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if "word/footer.xml" in files:
        footer = structured_docx.fromstring(files["word/footer.xml"])
        paragraph = footer.find("{" + W + "}p")
    else:
        footer = ET.Element("{" + W + "}ftr")
        paragraph = build.element(footer, "p")
        document = structured_docx.fromstring(files["word/document.xml"])
        section = document.find("{" + W + "}body/{" + W + "}sectPr")
        reference = build.element(section, "footerReference", type="default")
        reference.set("{" + R + "}id", "authoring_footer")
        section.remove(reference)
        section.insert(0, reference)
        relationships = structured_docx.fromstring(files["word/_rels/document.xml.rels"])
        ET.SubElement(
            relationships,
            "{" + REL + "}Relationship",
            {"Id": "authoring_footer", "Type": R + "/footer", "Target": "footer.xml"},
        )
        types = structured_docx.fromstring(files["[Content_Types].xml"])
        ET.SubElement(
            types,
            "{" + CT + "}Override",
            {
                "PartName": "/word/footer.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
            },
        )
        files["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        files["word/_rels/document.xml.rels"] = structured_docx.package_xml(relationships, REL)
        files["[Content_Types].xml"] = structured_docx.package_xml(types, CT)
    # Exact canonical field. No switches, child fields or instruction runs are generated.
    if list(paragraph):
        build.element(build.element(paragraph, "r"), "t", " ")
    field = build.element(paragraph, "fldSimple", instr=" PAGE ")
    build.element(build.element(field, "r"), "t", "1")
    files["word/footer.xml"] = ET.tostring(footer, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    result = output.getvalue()
    if len(result) > MAX_DOCX_BYTES:
        raise DomainError("AUTHORING_GENERATED_SCOPE_LIMIT", "生成Word超过64MiB范围", 422)
    return result


def producer_manifest(source, snapshot):
    return {
        "protocol": PROTOCOL,
        "artifact_kind": "bid_body",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "structure_digest": snapshot["authoring_structure"]["digest"],
        "sections_digest": sha(snapshot["sections"]),
        "generator_digest": sha(generator_identity()),
        "source_docx_digest": sha(source),
    }
