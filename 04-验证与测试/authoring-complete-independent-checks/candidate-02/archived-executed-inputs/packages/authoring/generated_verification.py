"""Pure generated-text lane: exact producer bytes AND closed OOXML/PAGE grammar.

This does not relax external Word inspection. A caller label is never authority.
"""

import io
import re
import stat
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring import generated_docx
from packages.authoring.build import W
from packages.domain.runtime import DomainError, sha

R = generated_docx.R
REL = generated_docx.REL
CT = generated_docx.CT
XML = "http://www.w3.org/XML/1998/namespace"
BASE_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/styles.xml",
    "word/numbering.xml",
    "word/_rels/document.xml.rels",
}
OPTIONAL_PARTS = {"word/header.xml", "word/footer.xml"}
ATTRS = {
    "document": set(),
    "body": set(),
    "p": set(),
    "pPr": set(),
    "r": set(),
    "rPr": set(),
    "t": {"xml:space"},
    "br": {"type"},
    "pStyle": {"val"},
    "ind": {"left"},
    "numPr": set(),
    "ilvl": {"val"},
    "numId": {"val"},
    "numbering": set(),
    "abstractNum": {"abstractNumId"},
    "lvl": {"ilvl"},
    "start": {"val"},
    "numFmt": {"val"},
    "lvlText": {"val"},
    "num": {"numId"},
    "abstractNumId": {"val"},
    "sectPr": set(),
    "pgSz": {"w", "h"},
    "pgMar": {"top", "right", "bottom", "left", "header", "footer", "gutter"},
    "styles": set(),
    "docDefaults": set(),
    "rPrDefault": set(),
    "rFonts": {"ascii", "hAnsi", "eastAsia"},
    "sz": {"val"},
    "style": {"type", "default", "styleId"},
    "name": {"val"},
    "basedOn": {"val"},
    "outlineLvl": {"val"},
    "keepNext": set(),
    "b": set(),
    "i": set(),
    "pPrDefault": set(),
    "spacing": {"after"},
    "hdr": set(),
    "ftr": set(),
    "headerReference": {"type", "r:id"},
    "footerReference": {"type", "r:id"},
    "fldSimple": {"instr"},
}
ROOTS = {
    "[Content_Types].xml": "{" + CT + "}Types",
    "_rels/.rels": "{" + REL + "}Relationships",
    "word/_rels/document.xml.rels": "{" + REL + "}Relationships",
    "word/document.xml": "{" + W + "}document",
    "word/styles.xml": "{" + W + "}styles",
    "word/numbering.xml": "{" + W + "}numbering",
    "word/header.xml": "{" + W + "}hdr",
    "word/footer.xml": "{" + W + "}ftr",
}


def blocked(message):
    raise DomainError("AUTHORING_GENERATED_PACKAGE_BLOCKED", message, 422)


def qualified(name):
    return (
        "{"
        + (XML if name.startswith("xml:") else R if name.startswith("r:") else W)
        + "}"
        + name.split(":")[-1]
    )


def validate_xml(name, raw):
    if len(raw) > 8 * 1024 * 1024 or b"\x00" in raw:
        blocked("生成XML大小或编码超范围")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        blocked("生成XML只接受UTF-8")
    if (
        "<!" in text
        or text.count("<?") > 1
        or not re.match(r"^<\?xml\s", text)
        or re.search(r"encoding\s*=\s*['\"](?!utf-8['\"])", text, re.I)
    ):
        blocked("生成XML不接受DTD、实体、处理指令或其他编码")
    try:
        # Bounded UTF-8 with no DTD, entities or processing instructions admitted.
        root = ET.fromstring(raw)  # nosec B314
    except ET.ParseError:
        blocked("生成XML不完整")
    if root.tag != ROOTS[name] or root.attrib:
        blocked("生成部件根与固定类型不符")
    parents = {child: parent for parent in root.iter() for child in parent}
    count = 0
    for item in root.iter():
        count += 1
        if count > 200000:
            blocked("生成XML节点数量超范围")
        if item.tag.startswith("{" + W + "}"):
            local = item.tag.split("}", 1)[1]
            allowed = ATTRS.get(local)
            if allowed is None or not set(item.attrib) <= {qualified(a) for a in allowed}:
                blocked("生成XML元素或属性不在固定生产器语法中")
            if local == "rFonts" and any(
                v not in {"Noto Sans CJK SC", "Noto Serif CJK SC"} for v in item.attrib.values()
            ):
                blocked("生成字体不在本地白名单")
            if local == "fldSimple":
                parent = parents.get(item)
                if (
                    name != "word/footer.xml"
                    or parent is None
                    or parent.tag != "{" + W + "}p"
                    or item.attrib != {"{" + W + "}instr": " PAGE "}
                ):
                    blocked("唯一动态域只允许固定页脚的规范PAGE")
                children = list(item)
                if len(children) != 1 or children[0].tag != "{" + W + "}r" or children[0].attrib:
                    blocked("PAGE不得包含嵌套域或其他run")
                texts = list(children[0])
                if (
                    len(texts) != 1
                    or texts[0].tag != "{" + W + "}t"
                    or texts[0].attrib
                    or list(texts[0])
                    or texts[0].text != "1"
                ):
                    blocked("PAGE仅接受固定缓存十进制文本")
        elif item.tag == "{" + REL + "}Relationships":
            pass
        elif item.tag == "{" + REL + "}Relationship":
            mapping = (
                {"rId1": (R + "/officeDocument", "word/document.xml")}
                if name == "_rels/.rels"
                else {
                    "rId1": (R + "/styles", "styles.xml"),
                    "rId2": (R + "/numbering", "numbering.xml"),
                    "authoring_header": (R + "/header", "header.xml"),
                    "authoring_footer": (R + "/footer", "footer.xml"),
                }
            )
            target = mapping.get(item.get("Id", ""))
            if target is None or item.attrib != {
                "Id": item.get("Id"),
                "Type": target[0],
                "Target": target[1],
            }:
                blocked("生成关系不得包含外部或未知目标")
        elif item.tag in {"{" + CT + "}Types", "{" + CT + "}Default", "{" + CT + "}Override"}:
            if name != "[Content_Types].xml":
                blocked("内容声明不得伪装其他部件")
        else:
            blocked("生成XML命名空间不受支持")
        if item.tag != "{" + W + "}t" and item.text and item.text.strip():
            blocked("生成文本只能位于普通文字节点")
        if item.tail and item.tail.strip():
            blocked("生成XML不得包含隐藏尾随文本")
    return root


def verify_container(source, *, page_number):
    if not isinstance(source, bytes) or len(source) > generated_docx.MAX_DOCX_BYTES:
        blocked("生成Word超过大小范围")
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            entries = archive.infolist()
            names = [i.filename for i in entries]
            if (
                len(names) != len(set(names))
                or not BASE_PARTS <= set(names)
                or not set(names) <= BASE_PARTS | OPTIONAL_PARTS
            ):
                blocked("生成Word部件清单不完整或含额外部件")
            total, roots = 0, {}
            for item in entries:
                total += item.file_size
                if (
                    item.flag_bits & 1
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.compress_type != zipfile.ZIP_DEFLATED
                    or item.file_size > 8 * 1024 * 1024
                    or total > 32 * 1024 * 1024
                    or item.file_size > max(item.compress_size, 1) * 200
                ):
                    blocked("生成ZIP资源或容器特性不在白名单")
                roots[item.filename] = validate_xml(item.filename, archive.read(item))
    except (zipfile.BadZipFile, OSError, RuntimeError):
        blocked("生成Word不是完整可读容器")
    types = roots["[Content_Types].xml"]
    defaults = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml",
    }
    overrides = {
        "/word/document.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "/word/styles.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        "/word/numbering.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        "/word/header.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
        "/word/footer.xml": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    }
    found_defaults, found_overrides = set(), set()
    for item in types:
        if item.tag == "{" + CT + "}Default":
            key = item.get("Extension")
            if (
                key not in defaults
                or key in found_defaults
                or item.attrib != {"Extension": key, "ContentType": defaults[key]}
            ):
                blocked("内容类型默认声明不在生成白名单")
            found_defaults.add(key)
        elif item.tag == "{" + CT + "}Override":
            key = item.get("PartName")
            if (
                key not in overrides
                or key in found_overrides
                or key[1:] not in roots
                or item.attrib != {"PartName": key, "ContentType": overrides[key]}
            ):
                blocked("内容类型覆盖声明与真实生成部件不符")
            found_overrides.add(key)
        else:
            blocked("内容类型子结构不在生成白名单")
    if found_defaults != set(defaults) or found_overrides != {
        "/" + name for name in roots if name.startswith("word/") and name.endswith(".xml")
    }:
        blocked("生成内容类型完整声明缺失")
    for name in ("_rels/.rels", "word/_rels/document.xml.rels"):
        ids = set()
        for item in roots[name]:
            identity, target = item.get("Id"), item.get("Target", "")
            if (
                identity in ids
                or (target if name == "_rels/.rels" else "word/" + target) not in roots
            ):
                blocked("生成关系重名或目标未声明/未存在")
            ids.add(identity)
    fields = [
        (name, item) for name, root in roots.items() for item in root.iter("{" + W + "}fldSimple")
    ]
    if len(fields) != int(page_number):
        blocked("页码域数量与冻结版式不一致")
    return roots


def verify(source, snapshot, manifest, generator_digest):
    if not isinstance(source, bytes) or len(source) > generated_docx.MAX_DOCX_BYTES:
        blocked("生成Word字节类型或大小超范围")
    expected_identity = sha(generated_docx.generator_identity())
    if generator_digest != expected_identity or manifest != generated_docx.producer_manifest(
        source, snapshot
    ):
        blocked("生成来源证明或白名单生产器摘要不一致")
    expected = generated_docx.assemble(snapshot)
    if source != expected:
        blocked("DOCX并非冻结正文与白名单生产器的精确产物")
    extension = snapshot["authoring_structure"]
    style = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    roots = verify_container(source, page_number=style["page_number"])
    return {
        "source_docx_digest": sha(source),
        "producer_manifest_digest": sha(manifest),
        "validator_digest": sha(Path(__file__).read_bytes()),
        "generator_digest": expected_identity,
        "parts": sorted(roots),
        "page_number": style["page_number"],
    }
