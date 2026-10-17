"""Separate PAGE-only generated-text sandbox lane. Static review required before use.

No HTTP entry, external-Word flag override, DB access, model call, or host renderer.
The existing Word03 sandbox functions are pinned and reused without modifying them.
"""

import json
import subprocess  # nosec B404
import tempfile
import time
import unicodedata
from pathlib import Path
from uuid import uuid4

import pypdfium2

from packages.authoring import generated_docx, generated_verification
from packages.authoring.build import W
from packages.domain.runtime import DomainError, sha
from packages.word_intake import renderer as sandbox

PROTOCOL = "authoring-generated-sandbox/1"
WORD03 = {
    "renderer.py": "547b94cb6f194bd011a9606e5d2b1f54b6c27a2ee6567da3a50e3083e417760e",
    "inspection.py": "76032305050a35450cf96272708ac0db3ad3d1eafddf78ff80e74b7f84339792",
    "sandbox_runner.py": "ec7069c4086fdfc6805ae34742b980d64484e8f37029833f5fdb9399763f9289",
}


def profile_for(base):
    if (
        not isinstance(base, dict)
        or set(base) != {"identity", "profile_digest"}
        or not isinstance(base["identity"], dict)
    ):
        raise DomainError("AUTHORING_SANDBOX_PROFILE_INVALID", "基础Word沙箱配置不完整", 503)
    for name, digest in WORD03.items():
        if sha(Path(sandbox.__file__).with_name(name).read_bytes()) != digest:
            raise DomainError(
                "AUTHORING_SANDBOX_DEPENDENCY_CHANGED", "已审Word沙箱端口源已变，需重新核验", 503
            )
    if (
        sha(base["identity"]) != base["profile_digest"]
        or base["identity"].get("protocol") != "word-docker-render/1"
        or base["identity"].get("adapter_source_digest") != WORD03["renderer.py"]
        or base["identity"].get("inspection_source_digest") != WORD03["inspection.py"]
    ):
        raise DomainError(
            "AUTHORING_SANDBOX_PROFILE_INVALID", "生成正文需精确已审Word03基础配置", 503
        )
    identity = {
        "protocol": PROTOCOL,
        "base_profile": base,
        "adapter_digest": sha(Path(__file__).read_bytes()),
        "generator_digest": sha(generated_docx.generator_identity()),
        "validator_digest": sha(Path(generated_verification.__file__).read_bytes()),
        "dynamic_field_policy": "one_canonical_footer_PAGE_only",
        "correspondence_policy": "complete_body_plus_per_page_geometric_header_footer_v1",
    }
    return {"identity": identity, "profile_digest": sha(identity)}


def prepare(source, snapshot, manifest, profile):
    if (
        not isinstance(profile, dict)
        or set(profile) != {"identity", "profile_digest"}
        or not isinstance(profile["identity"], dict)
        or "base_profile" not in profile["identity"]
    ):
        raise DomainError("AUTHORING_SANDBOX_PROFILE_INVALID", "生成正文转换配置不完整", 503)
    expected = profile_for(profile["identity"]["base_profile"])
    if profile != expected:
        raise DomainError("AUTHORING_SANDBOX_PROFILE_CHANGED", "生成器、语法或转换配置已变化", 409)
    proof = generated_verification.verify(
        source, snapshot, manifest, profile["identity"]["generator_digest"]
    )
    extension = snapshot["authoring_structure"]
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    roots = generated_verification.verify_container(source, page_number=config["page_number"])
    return {"proof": proof, "config": config, "roots": roots}


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def expected_body(roots):
    """Native list markers are rendered text too; preserve every body digit."""
    numbering = roots["word/numbering.xml"]
    formats = {
        n.get("{" + W + "}abstractNumId"): n.find("{" + W + "}lvl/{" + W + "}numFmt").get(
            "{" + W + "}val"
        )
        for n in numbering.findall("{" + W + "}abstractNum")
    }
    numbers = {
        n.get("{" + W + "}numId"): n.find("{" + W + "}abstractNumId").get("{" + W + "}val")
        for n in numbering.findall("{" + W + "}num")
    }
    counters: dict[str, int] = {}
    pieces = []
    for paragraph in roots["word/document.xml"].iter("{" + W + "}p"):
        number = paragraph.find("{" + W + "}pPr/{" + W + "}numPr/{" + W + "}numId")
        if number is not None:
            key = number.get("{" + W + "}val")
            counters[key] = counters.get(key, 0) + 1
            pieces.append("•" if formats[numbers[key]] == "bullet" else str(counters[key]) + ".")
        pieces.extend(item.text or "" for item in paragraph.iter("{" + W + "}t"))
    return "".join(pieces)


def inspect_pdf(data, prepared):
    config = prepared["config"]
    source_text = expected_body(prepared["roots"])
    gaps, body, page_records = [], [], []
    pdf = pypdfium2.PdfDocument(data)
    try:
        count = len(pdf)
        if not 0 < count <= 1000:
            raise ValueError("AUTHORING_PDF_PAGE_LIMIT")
        for index in range(count):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                width, height = page.get_size()
                if (
                    abs(width - 11906 / 20) > 1
                    or abs(height - 16838 / 20) > 1
                    or page.get_rotation() != 0
                ):
                    gaps.append("AUTHORING_PAGE_GEOMETRY_UNVERIFIED")
                footer_top = config["margin_bottom_twips"] / 20 - 3
                header_bottom = height - config["margin_top_twips"] / 20 + 3
                if (
                    footer_top < 36 + config["font_size_halfpoints"] / 2 + 3
                    or height - header_bottom < 36 + config["font_size_halfpoints"] / 2 + 3
                ):
                    gaps.append("AUTHORING_HEADER_FOOTER_REGION_OVERLAP")
                footer, header, main = [], [], []
                for character in range(textpage.count_chars()):
                    text = textpage.get_text_range(index=character, count=1)
                    if not text or text.isspace():
                        continue
                    left, bottom, right, top = textpage.get_charbox(character)
                    if left < -1 or right > width + 1 or bottom < -1 or top > height + 1:
                        gaps.append("AUTHORING_TEXT_OUTSIDE_PAGE")
                    if any(c in text for c in ("\ufffd", "\u25a1", "\u25a0")):
                        gaps.append("AUTHORING_PDF_GLYPH_UNVERIFIED")
                    if top <= footer_top:
                        footer.append(text)
                    elif bottom >= header_bottom:
                        header.append(text)
                    else:
                        main.append(text)
                expected_footer = config["footer_text"] + (
                    str(index + 1) if config["page_number"] else ""
                )
                if normalize("".join(footer)) != normalize(expected_footer):
                    gaps.append("AUTHORING_PAGE_NUMBER_OR_FOOTER_UNVERIFIED")
                if normalize("".join(header)) != normalize(config["header_text"]):
                    gaps.append("AUTHORING_HEADER_UNVERIFIED")
                body.append("".join(main))
                page_records.append(
                    {
                        "physical_page": index + 1,
                        "footer_matches": normalize("".join(footer)) == normalize(expected_footer),
                        "header_matches": normalize("".join(header))
                        == normalize(config["header_text"]),
                        "body_character_count": len("".join(main)),
                    }
                )
            finally:
                textpage.close()
                page.close()
    finally:
        pdf.close()
    if normalize("".join(body)) != normalize(source_text):
        gaps.append("AUTHORING_COMPLETE_BODY_TEXT_UNVERIFIED")
    if not source_text.strip():
        gaps.append("AUTHORING_TEXT_SCOPE_EMPTY")
    return {
        "page_count": count,
        "gaps": list(dict.fromkeys(gaps)),
        "pages": page_records,
        "expected_body_digest": sha(normalize(source_text)),
        "actual_body_digest": sha(normalize("".join(body))),
    }


def failure(source, profile, code, *, attempted=False, isolation=None):
    return {
        "state": "failed",
        "coverage": "failed",
        "conversion_attempted": attempted,
        "error_code": code,
        "retryable": code
        not in {
            "AUTHORING_GENERATED_PACKAGE_BLOCKED",
            "AUTHORING_SANDBOX_PROFILE_INVALID",
            "AUTHORING_SANDBOX_PROFILE_CHANGED",
        },
        "gaps": [code],
        "chain": {
            "source_docx_digest": sha(source),
            "profile_digest": profile.get("profile_digest") if isinstance(profile, dict) else None,
        },
        "isolation": isolation,
        "pdf": None,
    }


def render(source, snapshot, manifest, profile):
    """No caller DB/remote transaction. Validate completely before starting a container."""
    try:
        prepared = prepare(source, snapshot, manifest, profile)
    except DomainError as error:
        return failure(source, profile, error.code)
    base = profile["identity"]["base_profile"]
    try:
        if sandbox.build_profile() != base:
            return failure(source, profile, "AUTHORING_BASE_SANDBOX_CHANGED")
    except (DomainError, OSError, subprocess.SubprocessError):
        return failure(source, profile, "AUTHORING_BASE_SANDBOX_UNAVAILABLE")
    name = "twb-authoring-" + uuid4().hex
    isolation, outcome, process = None, None, None
    attempted = False
    with tempfile.TemporaryDirectory(prefix="authoring-generated-") as directory:
        path = Path(directory) / "source.docx"
        path.write_bytes(source)
        path.chmod(0o444)
        try:
            sandbox._docker(sandbox._arguments(name, path, base), directory)
            isolation = sandbox._inspect_isolation(name, directory, base)
            attempted = True
            process = subprocess.Popen(
                [
                    sandbox.DOCKER,
                    "--host",
                    "unix:///var/run/docker.sock",
                    "start",
                    "--attach",
                    name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=sandbox._host_environment(directory),
                start_new_session=True,
            )  # nosec B603
            encoded = sandbox._read_process(process, time.monotonic() + sandbox.OPTIONS["timeout"])
            if process.returncode != 0:
                return failure(
                    source,
                    profile,
                    "AUTHORING_GENERATED_CONVERSION_FAILED",
                    attempted=True,
                    isolation=isolation,
                )
            header, data = encoded.split(b"\n", 1) if b"\n" in encoded else (encoded.strip(), b"")
            status = json.loads(header)
            if status.get("state") != "succeeded":
                return failure(
                    source,
                    profile,
                    "AUTHORING_GENERATED_CONVERSION_TIMEOUT"
                    if status.get("error_code") == "WORD_CONVERSION_TIMEOUT"
                    else "AUTHORING_GENERATED_CONVERSION_FAILED",
                    attempted=True,
                    isolation=isolation,
                )
            if (
                not data.startswith(b"%PDF-")
                or not 0 < len(data) <= sandbox.MAX_PDF
                or status.get("byte_size") != len(data)
            ):
                return failure(
                    source,
                    profile,
                    "AUTHORING_GENERATED_PDF_INVALID",
                    attempted=True,
                    isolation=isolation,
                )
            correspondence = inspect_pdf(data, prepared)
            fonts = sandbox._font_gaps(
                {
                    "requested_fonts": [prepared["config"]["font"]],
                    "paragraphs": [
                        expected_body(prepared["roots"]),
                        prepared["config"]["header_text"],
                        prepared["config"]["footer_text"],
                    ],
                },
                directory,
            )
            gaps = list(dict.fromkeys(correspondence["gaps"] + fonts))
            outcome = {
                "state": "succeeded",
                "coverage": "partial" if gaps else "complete",
                "conversion_attempted": True,
                "error_code": None,
                "retryable": False,
                "gaps": gaps,
                "chain": prepared["proof"]
                | {
                    "pdf_digest": sha(data),
                    "profile_digest": profile["profile_digest"],
                    "base_profile_digest": base["profile_digest"],
                    "renderer_digest": sha(
                        {
                            "base": base["identity"]["renderer_digest"],
                            "adapter": profile["identity"]["adapter_digest"],
                        }
                    ),
                    "fontset_digest": base["identity"]["fontset_digest"],
                    "options_digest": sha(
                        {
                            "base": base["identity"]["options_digest"],
                            "policy": profile["identity"]["dynamic_field_policy"],
                        }
                    ),
                    "page_count": correspondence["page_count"],
                },
                "correspondence": correspondence,
                "isolation": isolation,
                "pdf": data,
            }
            return outcome
        except (TimeoutError, subprocess.TimeoutExpired):
            return failure(
                source,
                profile,
                "AUTHORING_GENERATED_CONVERSION_TIMEOUT",
                attempted=attempted,
                isolation=isolation,
            )
        except (
            DomainError,
            OSError,
            ValueError,
            subprocess.SubprocessError,
            pypdfium2.PdfiumError,
        ):
            return failure(
                source,
                profile,
                "AUTHORING_GENERATED_CONVERSION_FAILED",
                attempted=attempted,
                isolation=isolation,
            )
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process and process.stdout:
                process.stdout.close()
            try:
                sandbox._docker(["rm", "--force", "--volumes", name], directory)
                if isolation is not None:
                    isolation["own_job_cleanup_confirmed"] = True
            except (OSError, subprocess.SubprocessError):
                if isolation is not None:
                    isolation["own_job_cleanup_confirmed"] = False
                if outcome is not None:
                    outcome["coverage"] = "partial"
                    outcome["gaps"].append("AUTHORING_SANDBOX_CLEANUP_UNCONFIRMED")
