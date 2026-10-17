"""RICH-only sandbox adapter. Requires independent concrete static review.

Never changes external Word03 or the frozen PAGE2 lane. No DB/model/HTTP entry.
"""

import json
import subprocess  # nosec B404
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pypdfium2

from packages.authoring import generated_renderer as text_renderer
from packages.authoring import page_navigation, rich_docx, rich_pdf, rich_verification
from packages.domain.runtime import DomainError, sha

sandbox = text_renderer.sandbox
failure = text_renderer.failure
expected_body = text_renderer.expected_body
PROTOCOL = "authoring-rich-sandbox/3"
MAX_LAYOUT_PASSES = 3


def profile_for(base):
    text_renderer.profile_for(base)  # Exact approved Word03 source/base identity; no rendering.
    identity = {
        "protocol": PROTOCOL,
        "base_profile": base,
        "adapter_digest": sha(Path(__file__).read_bytes()),
        "generator_digest": sha(rich_docx.identity()),
        "validator_digest": sha(Path(rich_verification.__file__).read_bytes()),
        "text_inspector_digest": sha(Path(text_renderer.__file__).read_bytes()),
        "raster_inspector_digest": sha(Path(rich_pdf.__file__).read_bytes()),
        "navigation_inspector_digest": sha(Path(page_navigation.__file__).read_bytes()),
        "dynamic_field_policy": "one_canonical_footer_PAGE_only",
        "navigation_policy": "stable_internal_bookmarks_and_TOC_hyperlinks_only",
        "correspondence_policy": "full_text_digits_geometry_opaque_raster_isolated_PDFium_all_pixels_direct_dest_TOC_v2",
        "max_layout_passes": MAX_LAYOUT_PASSES,
        "max_image_placement_pixels": 32_000_000,
        "max_normalized_image_bytes": 64 * 1024 * 1024,
        "max_visibility_render_pixels": rich_pdf.MAX_VISIBILITY_RENDER_PIXELS,
        "table_layout_policy": "native_grid_and_complete_cell_text_PDF_geometry_pending_partial",
    }
    return {"identity": identity, "profile_digest": sha(identity)}


def prepare(source, snapshot, materials, manifest, profile, *, pagination=None):
    if (
        not isinstance(profile, dict)
        or set(profile) != {"identity", "profile_digest"}
        or not isinstance(profile["identity"], dict)
        or "base_profile" not in profile["identity"]
    ):
        raise DomainError("AUTHORING_RICH_PROFILE_INVALID", "表图生成的受控转换配置不完整", 503)
    if profile != profile_for(profile["identity"]["base_profile"]):
        raise DomainError(
            "AUTHORING_RICH_PROFILE_CHANGED", "表图生成器、验证器或版面配置已变化", 409
        )
    proof = rich_verification.verify(
        source,
        snapshot,
        materials,
        manifest,
        profile["identity"]["generator_digest"],
        pagination=pagination,
    )
    extension = snapshot["authoring_structure"]
    config = extension["metadata"].get("effective_style", extension["style_template"]["config"])
    roots = rich_verification.verify_container(source, materials, page_number=config["page_number"])
    return {
        "proof": proof,
        "config": config,
        "roots": roots,
        "snapshot": snapshot,
        "materials": materials,
        "pagination": pagination,
    }


def inspect_pdf(data, prepared):
    body = text_renderer.inspect_pdf(data, prepared)
    images = rich_pdf.inspect_images(
        data, prepared["snapshot"], prepared["materials"], prepared["config"]
    )
    mode = prepared["pagination"]["mode"] if prepared["pagination"] else "continuous"
    navigation = page_navigation.inspect_navigation(
        data,
        rich_docx.navigation_entries(prepared["snapshot"], prepared["pagination"]),
        mode=mode,
        style=prepared["config"],
    )
    table_gaps = (
        ["AUTHORING_TABLE_PDF_GEOMETRY_UNVERIFIED"]
        if prepared["proof"]["native_table_count"]
        else []
    )
    return body | {
        "gaps": list(
            dict.fromkeys(body["gaps"] + images["gaps"] + navigation["gaps"] + table_gaps)
        ),
        "raster_correspondence": images,
        "navigation": navigation,
    }


def render_once(source, snapshot, materials, manifest, profile, *, pagination=None):
    """No caller DB/remote transaction. Validate completely before starting a container."""
    try:
        prepared = prepare(source, snapshot, materials, manifest, profile, pagination=pagination)
    except DomainError as error:
        return failure(source, profile, error.code)
    base = profile["identity"]["base_profile"]
    try:
        if sandbox.build_profile() != base:
            return failure(source, profile, "AUTHORING_BASE_SANDBOX_CHANGED")
    except (DomainError, OSError, subprocess.SubprocessError):
        return failure(source, profile, "AUTHORING_BASE_SANDBOX_UNAVAILABLE")
    name = "twb-authoring-rich-" + uuid4().hex
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


def render(snapshot, materials, profile, *, mode):
    """At most three actual renders; retries only reflect measured TOC reflow.

    Returns final DOCX/PDF plus exact internal iteration bytes for the caller to
    retain as immutable private blobs. The API never accepts these proofs as input.
    """
    if mode not in {"chapter_start_new_page", "continuous"}:
        raise DomainError("AUTHORING_PAGINATION_INVALID", "请选择明确的章页排版模式", 422)
    entries = rich_docx.navigation_entries(snapshot)
    pagination = {"mode": mode, "toc_start_pages": {entry["node_id"]: None for entry in entries}}
    iterations, summaries = [], []
    final = None
    for index in range(MAX_LAYOUT_PASSES):
        source = rich_docx.assemble(snapshot, materials, pagination=pagination)
        manifest = rich_verification.manifest(source, snapshot, materials, pagination=pagination)
        # DOCX-only/unconfigured rendering still admits only complete producer bytes.
        rich_verification.verify(
            source, snapshot, materials, manifest, sha(rich_docx.identity()), pagination=pagination
        )
        if profile is None:
            final = failure(source, {}, "AUTHORING_RICH_RENDER_NOT_CONFIGURED")
        else:
            final = render_once(
                source, snapshot, materials, manifest, profile, pagination=pagination
            )
        final["docx"] = source
        final["producer_manifest"] = manifest
        final["pagination"] = pagination
        if final["state"] != "succeeded" or not isinstance(final.get("pdf"), bytes):
            break
        entries = rich_docx.navigation_entries(snapshot, pagination)
        iterations.append({"pdf_bytes": final["pdf"], "docx_bytes": source, "entries": entries})
        navigation = final["correspondence"]["navigation"]
        summaries.append(
            {
                "iteration": index + 1,
                "docx_digest": sha(source),
                "pdf_digest": sha(final["pdf"]),
                "pagination": pagination,
                "navigation": navigation,
                "gaps": final["gaps"],
            }
        )
        if navigation["navigation_coverage"] != "complete" or not navigation["toc_values_verified"]:
            break
        measured = {entry["node_id"]: entry["start_page"] for entry in navigation["entries"]}
        if (
            any(page is None for page in measured.values())
            or measured == pagination["toc_start_pages"]
        ):
            break
        pagination = {"mode": mode, "toc_start_pages": measured}
    if final is None:
        raise RuntimeError("RICH render loop did not produce an admitted DOCX")
    final["render_iterations"] = iterations
    final["iteration_summaries"] = summaries
    if iterations:
        extension = snapshot["authoring_structure"]
        style = extension["metadata"].get("effective_style", extension["style_template"]["config"])
        context_digest = sha(
            {
                "snapshot_digest": snapshot["snapshot_digest"],
                "sections_digest": sha(snapshot["sections"]),
                "structure_digest": extension["digest"],
                "materials": [
                    {k: v for k, v in item.items() if k != "bytes"} for item in materials
                ],
                "renderer_profile": profile,
                "mode": mode,
            }
        )
        fixed = page_navigation.converge_navigation(
            iterations, context_digest=context_digest, mode=mode, style=style
        )
        final_matches_iteration = (
            final["state"] == "succeeded"
            and final.get("pdf") == iterations[-1]["pdf_bytes"]
            and final["docx"] == iterations[-1]["docx_bytes"]
        )
        final["navigation_proof" if final_matches_iteration else "navigation_history_proof"] = fixed
        final["gaps"] = list(dict.fromkeys(final["gaps"] + fixed["gaps"]))
        if not fixed["fixed_point"] and len(iterations) == MAX_LAYOUT_PASSES:
            final["gaps"].append("AUTHORING_TOC_LAYOUT_DID_NOT_CONVERGE")
            final["adjustment_hint"] = (
                "目录数字仍引起分页变化；请缩短长目录标题、调整字体或版心后重新生成，勿使用计划页数替代。"
            )
        if final["state"] == "succeeded":
            final["coverage"] = "partial" if final["gaps"] else "complete"
    return final
