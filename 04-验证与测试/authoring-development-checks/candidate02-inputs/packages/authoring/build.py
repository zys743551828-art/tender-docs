"""Actual OOXML generation and LibreOffice conversion; format results stay independent."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import signal

# Trusted renderer argv only, never a shell.
import subprocess  # nosec B404
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Serialization only; no XML is parsed in this module.
from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring.content import validate_content
from packages.domain.runtime import DomainError, sha

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("w", W)
OPTIONS = {
    "page": "A4",
    "margin_twips": 1440,
    "font": "Noto Sans CJK SC",
    "font_size_halfpoints": 22,
    "renderer_filter": "writer_pdf_Export",
    "content_subset": 1,
}
TEMPLATE_DIGEST = sha(OPTIONS)
WRITER_DIGEST = sha(Path(__file__).read_bytes())


def element(parent, name, text=None, **attrs):
    el = ET.SubElement(
        parent, "{" + W + "}" + name, {"{" + W + "}" + k: str(v) for k, v in attrs.items()}
    )
    el.text = text
    return el


def assemble_docx(snapshot: dict) -> bytes:
    """Only body content is emitted; citations, UI and findings never leak into body."""
    if not snapshot.get("sections"):
        raise DomainError("DRAFT_EMPTY", "草稿尚无章节，无法生成", 409)
    document = ET.Element("{" + W + "}document")
    body = element(document, "body")
    numbering = ET.Element("{" + W + "}numbering")
    for index, fmt, label in [(0, "decimal", "%1."), (1, "bullet", "•")]:
        abstract = element(numbering, "abstractNum", abstractNumId=index)
        level = element(abstract, "lvl", ilvl=0)
        element(level, "start", val=1)
        element(level, "numFmt", val=fmt)
        element(level, "lvlText", val=label)
    numbering_count = 0
    for section in snapshot["sections"]:
        validate_content(section)
        p = element(body, "p")
        element(element(p, "pPr"), "pStyle", val="Heading1")
        element(element(p, "r"), "t", section["attrs"]["title"])

        def write_node(node, list_depth=0, num_id=None):
            nonlocal numbering_count
            kind = node["type"]
            if kind in ("bulletList", "orderedList"):
                numbering_count += 1
                own_id = numbering_count
                num = element(numbering, "num", numId=own_id)
                element(num, "abstractNumId", val=0 if kind == "orderedList" else 1)
                for child in node["content"]:
                    write_node(child, list_depth + 1, own_id)
                return
            if kind == "listItem":
                for index, child in enumerate(node["content"]):
                    write_node(child, list_depth, num_id if index == 0 else None)
                return
            paragraph = element(body, "p")
            pr = element(paragraph, "pPr")
            if kind == "heading":
                element(pr, "pStyle", val="Heading" + str(min(node["attrs"]["level"] + 1, 6)))
            if list_depth:
                element(pr, "ind", left=360 * list_depth)
            if num_id is not None:
                np = element(pr, "numPr")
                element(np, "ilvl", val=0)
                element(np, "numId", val=num_id)
            for child in node["content"]:
                run = element(paragraph, "r")
                if child["type"] == "hardBreak":
                    element(run, "br")
                else:
                    if child.get("marks"):
                        props = element(run, "rPr")
                        for mark in child["marks"]:
                            element(props, "b" if mark["type"] == "bold" else "i")
                    t = element(run, "t", child["text"])
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

        for node in section["content"]:
            write_node(node)
    section_props = element(body, "sectPr")
    element(section_props, "pgSz", w=11906, h=16838)
    element(
        section_props,
        "pgMar",
        top=1440,
        right=1440,
        bottom=1440,
        left=1440,
        header=720,
        footer=720,
        gutter=0,
    )
    styles = ET.Element("{" + W + "}styles")
    defaults = element(styles, "docDefaults")
    props = element(element(defaults, "rPrDefault"), "rPr")
    element(props, "rFonts", ascii=OPTIONS["font"], hAnsi=OPTIONS["font"], eastAsia=OPTIONS["font"])
    element(props, "sz", val=22)
    normal = element(styles, "style", type="paragraph", default="1", styleId="Normal")
    element(normal, "name", val="Normal")
    for level in range(1, 7):
        heading = element(styles, "style", type="paragraph", styleId=f"Heading{level}")
        element(heading, "name", val=f"heading {level}")
        element(heading, "basedOn", val="Normal")
        pp = element(heading, "pPr")
        element(pp, "outlineLvl", val=level - 1)
        element(pp, "keepNext")
        rr = element(heading, "rPr")
        element(rr, "b")
        element(rr, "sz", val=32 - level * 2)
    files = {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>',
        "word/document.xml": ET.tostring(document, encoding="utf-8", xml_declaration=True),
        "word/numbering.xml": ET.tostring(numbering, encoding="utf-8", xml_declaration=True),
        "word/styles.xml": ET.tostring(styles, encoding="utf-8", xml_declaration=True),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return out.getvalue()


@dataclass(frozen=True)
class RenderResult:
    state: str
    pdf: bytes | None
    chain: dict
    gaps: tuple[str, ...]


def renderer_environment(directory=None):
    # Child renderer never receives API/Worker DSNs, cloud secrets or model keys.
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    values = {key: value for key, value in os.environ.items() if key in allowed}
    if directory:
        values["TMPDIR"] = directory
    return values


def render_identity(executable: str, font_path: str) -> dict:
    """Freeze trusted executable/version and actual font bytes before enqueue."""
    resolved = shutil.which(executable)
    if not resolved or not Path(font_path).is_file():
        raise DomainError("RENDERER_UNAVAILABLE", "Word原件可生成，PDF转换器或字体尚未配置", 503)
    try:
        version = (
            subprocess.run(
                [resolved, "--version"],
                check=True,
                capture_output=True,
                timeout=10,
                env=renderer_environment(),
            )  # nosec B603
            .stdout.decode(errors="replace")
            .strip()
        )  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        raise DomainError("RENDERER_UNAVAILABLE", "PDF转换器当前不可用", 503) from None
    return {
        "renderer_digest": sha(
            {
                "launcher_sha256": hashlib.sha256(Path(resolved).read_bytes()).hexdigest(),
                "version": version,
            }
        ),
        "renderer_version": version,
        "fontset_digest": hashlib.sha256(Path(font_path).read_bytes()).hexdigest(),
        "options_digest": TEMPLATE_DIGEST,
    }


def render_pdf(
    docx: bytes,
    *,
    executable: str,
    font_path: str,
    expected_identity: dict | None = None,
    timeout: int = 60,
    sandbox_attested: bool = False,
) -> RenderResult:
    """The caller supplies a trusted local binary/container wrapper, never client input.

    Generated OOXML has no external relationships. Production must provide a no-network,
    nonroot, read-only-root, resource-limited wrapper and attest that profile separately.
    """
    chain = {"source_docx_digest": sha(docx), "options_digest": TEMPLATE_DIGEST}
    gaps = [] if sandbox_attested else ["RENDER_SANDBOX_NOT_ATTESTED"]
    try:
        identity = render_identity(executable, font_path)
        chain.update(identity)
        if expected_identity and any(
            identity.get(key) != value for key, value in expected_identity.items()
        ):
            return RenderResult("failed", None, chain, ("RENDER_PROFILE_CHANGED",))
        if len(docx) > 20_000_000:
            return RenderResult("failed", None, chain, ("RENDER_INPUT_TOO_LARGE",))
        with tempfile.TemporaryDirectory(prefix="tender-render-") as directory:
            path = Path(directory)
            source = path / "body.docx"
            source.write_bytes(docx)
            os.chmod(source, 0o600)
            command = [
                executable,
                "-env:UserInstallation=" + (path / "profile").as_uri(),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(path),
                str(source),
            ]
            with subprocess.Popen(  # nosec B603
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                cwd=path,
                env=renderer_environment(str(path)),
            ) as process:
                try:
                    process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise
                returncode = process.returncode
            target = path / "body.pdf"
            if returncode or not target.is_file():
                return RenderResult("failed", None, chain, tuple(gaps + ["PDF_CONVERSION_FAILED"]))
            if target.stat().st_size > 50_000_000:
                return RenderResult("failed", None, chain, tuple(gaps + ["PDF_OUTPUT_TOO_LARGE"]))
            pdf = target.read_bytes()
            if not pdf.startswith(b"%PDF-"):
                return RenderResult("failed", None, chain, tuple(gaps + ["PDF_INVALID"]))
            import pypdfium2

            parsed = pypdfium2.PdfDocument(pdf)
            try:
                if len(parsed) < 1:
                    return RenderResult("failed", None, chain, ("PDF_EMPTY",))
                chain["page_count"] = len(parsed)
            finally:
                parsed.close()
            chain["pdf_digest"] = sha(pdf)
            return RenderResult("succeeded", pdf, chain, tuple(gaps))
    except subprocess.TimeoutExpired:
        return RenderResult("failed", None, chain, tuple(gaps + ["PDF_CONVERSION_TIMEOUT"]))
    except (DomainError, OSError, ValueError):
        return RenderResult("failed", None, chain, tuple(gaps + ["RENDERER_UNAVAILABLE"]))
