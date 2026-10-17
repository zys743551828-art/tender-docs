"""Fixed local-daemon Docker CPU sandbox for external Word, no host credentials.

The private profile is installed explicitly; requests never pull images, download
assets, choose host paths, change filters or invoke a shell.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess  # nosec B404
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

import pypdfium2

from packages.domain.runtime import DomainError, sha
from packages.word_intake.inspection import inspect_docx

ROOT = Path(__file__).resolve().parents[2]
DOCKER = "/usr/local/bin/docker"
IMAGE = "sha256:327daa8fae7178d61f93142f146b098467b345e10997b9eb79f63bd58e5c8f3c"
MOUNTS = {
    "/opt/codex/runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice": "/lo",
    "/usr/lib/x86_64-linux-gnu": "/usr/lib/x86_64-linux-gnu",
    "/usr/share/fonts": "/usr/share/fonts",
    "/etc/fonts": "/etc/fonts",
    "/usr/share/fontconfig": "/usr/share/fontconfig",
    "/usr/bin/python3.13": "/runner/python",
    "/usr/lib/python3.13": "/usr/lib/python3.13",
    str(Path(__file__).with_name("sandbox_runner.py")): "/runner/run.py",
}
MAX_PDF = 50 * 1024 * 1024
OPTIONS: dict[str, Any] = {
    "filter": "writer_pdf_Export",
    "network": "none",
    "ipc": "none",
    "user": "65534:65534",
    "read_only": True,
    "cap_drop": "ALL",
    "no_new_privileges": True,
    "memory": 512 * 1024 * 1024,
    "cpus": 1,
    "pids": 64,
    "tmp_bytes": 128 * 1024 * 1024,
    "output_bytes": 64 * 1024 * 1024,
    "timeout": 50,
    "max_pdf_bytes": MAX_PDF,
}
CONTAINER_ENV = {
    "HOME": "/tmp",  # nosec B108
    "TMPDIR": "/tmp",  # nosec B108
    "LANG": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "PYTHONHOME": "/usr",
    "PYTHONPATH": "",
    "SAL_USE_VCLPLUGIN": "svp",
    "LD_LIBRARY_PATH": "/lo/program:/usr/lib/x86_64-linux-gnu",
}


def _file_digest(path):
    import hashlib

    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def _asset_manifest():
    records = {}
    content_cache = {}
    for source in [DOCKER, "/usr/bin/fc-match", *MOUNTS]:
        root = Path(source)
        if not root.exists():
            raise DomainError("WORD_RENDER_ASSET_MISSING", "隔离转换依赖未完整配置", 503)
        values = sorted(root.rglob("*")) if root.is_dir() else [root]
        own = {}
        for path in values:
            if not path.is_file():
                continue
            real = path.resolve()
            info = real.stat()
            cache_key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if cache_key not in content_cache:
                content_cache[cache_key] = _file_digest(real)
            own[str(path.relative_to(root)) if root.is_dir() else path.name] = content_cache[
                cache_key
            ]
        records[source] = sha(own)
    return records


def _host_environment(directory):
    # Explicit local daemon plus empty Docker config; no cloud, proxy, context or DSN inheritance.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directory),
        "DOCKER_CONFIG": str(Path(directory) / "docker-config"),
        "LANG": "C.UTF-8",
    }


def _docker(arguments, directory, timeout=15):
    return subprocess.run(
        [DOCKER, "--host", "unix:///var/run/docker.sock", *arguments],
        env=_host_environment(directory),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=True,
    )  # nosec B603


def build_profile():
    """Explicit installation/preflight only; no user document is needed here."""
    assets = _asset_manifest()
    with tempfile.TemporaryDirectory(prefix="word-profile-") as directory:
        image = json.loads(_docker(["image", "inspect", IMAGE], directory).stdout)[0]
    if image["Id"] != IMAGE:
        raise DomainError("WORD_RENDER_IMAGE_CHANGED", "转换镜像身份不匹配", 503)
    names = [item.split("=", 1)[0] for item in image["Config"].get("Env", [])]
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
        raise DomainError("WORD_RENDER_IMAGE_ENV_INVALID", "转换镜像配置不合法", 503)
    fonts = {
        path: value
        for path, value in assets.items()
        if path in {"/usr/share/fonts", "/etc/fonts", "/usr/share/fontconfig"}
    }
    identity = {
        "protocol": "word-docker-render/1",
        "image_id": IMAGE,
        "renderer_digest": sha({key: value for key, value in assets.items() if key not in fonts}),
        "fontset_digest": sha(fonts),
        "options_digest": sha(OPTIONS),
        "adapter_source_digest": _file_digest(Path(__file__)),
        "inspection_source_digest": _file_digest(Path(__file__).with_name("inspection.py")),
        "assets": assets,
        "image_environment_names": sorted(names),
        "options": OPTIONS,
    }
    return {"identity": identity, "profile_digest": sha(identity)}


def configured_profile():
    filename = os.environ.get("WORD_RENDER_PROFILE_PATH")
    if not filename:
        return {
            "identity": {"protocol": "word-docker-render/1", "enabled": False},
            "profile_digest": sha({"protocol": "word-docker-render/1", "enabled": False}),
        }
    try:
        value = json.loads(Path(filename).read_text())
    except (OSError, ValueError) as error:
        raise DomainError(
            "WORD_RENDER_PROFILE_UNAVAILABLE", "Word隔离转换配置不可读", 503
        ) from error
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("identity"), dict)
        or (
            sha(value["identity"]) != value.get("profile_digest")
            or value["identity"].get("image_id") != IMAGE
        )
    ):
        raise DomainError("WORD_RENDER_PROFILE_INVALID", "Word隔离转换配置身份不匹配", 503)
    return value


def failure(source, profile, code, *, attempted=False, retryable=False, isolation=None):
    return {
        "state": "failed",
        "coverage": "failed",
        "conversion_attempted": attempted,
        "error_code": code,
        "retryable": retryable,
        "gaps": [code],
        "chain": {
            "source_docx_digest": sha(source),
            "profile_digest": profile["profile_digest"],
            **{
                key: profile["identity"].get(key)
                for key in ("renderer_digest", "fontset_digest", "options_digest")
            },
        },
        "isolation": isolation,
        "pdf": None,
    }


def _arguments(name, source, profile):
    result = [
        "create",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--ipc",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--log-driver",
        "none",
        "--ulimit",
        "nofile=256:256",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=134217728,mode=1777",  # nosec B108
        "--tmpfs",
        "/output:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=1048576,mode=000",
    ]
    for path, target in MOUNTS.items():
        result.extend(["--mount", f"type=bind,src={path},dst={target},readonly"])
    result.extend(["--mount", f"type=bind,src={source},dst=/input/source.docx,readonly"])
    for key in profile["identity"]["image_environment_names"]:
        if key not in CONTAINER_ENV:
            result.extend(
                ["--env", key]
            )  # Absent from the clean host environment: remove image default.
    for key, value in CONTAINER_ENV.items():
        result.extend(["--env", key + "=" + value])
    return result + ["--entrypoint", "/runner/python", IMAGE, "-I", "/runner/run.py"]


def _inspect_isolation(name, directory, profile):
    value = json.loads(_docker(["inspect", name], directory).stdout)[0]
    host, config = value["HostConfig"], value["Config"]
    observed_env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
    removed_image_env = {item for item in config.get("Env", []) if "=" not in item}
    expected_removed = set(profile["identity"]["image_environment_names"]) - CONTAINER_ENV.keys()
    expected_binds = {
        (str(Path(source).resolve()), target) for source, target in MOUNTS.items()
    } | {(str((Path(directory) / "source.docx").resolve()), "/input/source.docx")}
    observed_binds = {
        (str(Path(item["Source"]).resolve()), item["Destination"])
        for item in value["Mounts"]
        if item["Type"] == "bind"
    }
    checks = {
        "local_image_pinned": value["Image"] == IMAGE,
        "network_none": host["NetworkMode"] == "none",
        "ipc_none": host["IpcMode"] == "none",
        "pid_private": host["PidMode"] == "",
        "uts_private": host["UTSMode"] == "",
        "nonroot": config["User"] == "65534:65534",
        "read_only": host["ReadonlyRootfs"],
        "not_privileged": not host["Privileged"],
        "capabilities_dropped": host["CapDrop"] == ["ALL"],
        "no_new_privileges": "no-new-privileges" in host["SecurityOpt"],
        "memory_limited": host["Memory"] == 512 * 1024 * 1024
        and host["MemorySwap"] == host["Memory"],
        "cpu_limited": host["NanoCpus"] == 1_000_000_000,
        "pids_limited": host["PidsLimit"] == 64,
        "no_daemon_document_logs": host["LogConfig"]["Type"] == "none",
        "clean_environment": observed_env == CONTAINER_ENV
        and removed_image_env == expected_removed,
        "all_bind_mounts_readonly": all(
            not item["RW"] for item in value["Mounts"] if item["Type"] == "bind"
        ),
        "exact_allowlisted_bind_mounts": observed_binds == expected_binds,
        "no_anonymous_persistent_volumes": all(
            item["Type"] in {"bind", "tmpfs"} for item in value["Mounts"]
        ),
        "image_data_volume_replaced_by_bounded_inaccessible_tmpfs": "mode=000"
        in host.get("Tmpfs", {}).get("/var/lib/postgresql/data", ""),
        "no_docker_socket_mount": all(
            "docker.sock" not in item["Destination"] for item in value["Mounts"]
        ),
        "bounded_tmpfs_output": "/output" in host.get("Tmpfs", {})
        and "size=67108864" in host["Tmpfs"]["/output"],
    }
    if not all(checks.values()):
        raise DomainError(
            "WORD_RENDER_ISOLATION_MISMATCH", "真实转换隔离配置不符合冻结profile", 503
        )
    return checks


def _read_process(process, deadline):
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError
            for key, _ in selector.select(timeout=0.1):
                block = os.read(key.fd, 65536)
                if not block:
                    process.wait(timeout=5)
                    return bytes(result)
                result.extend(block)
                if len(result) > MAX_PDF + 4096:
                    raise ValueError("WORD_PDF_OUTPUT_LIMIT")


def _font_gaps(inspection, directory):
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(directory),
        "LANG": "C.UTF-8",
        "FONTCONFIG_FILE": "/etc/fonts/fonts.conf",
        "FONTCONFIG_PATH": "/etc/fonts",
    }
    gaps = []
    for name in inspection["requested_fonts"]:
        if not re.fullmatch(r"[\w ,.-]{1,128}", name) or name.startswith("-"):
            gaps.append("WORD_FONT_NAME_UNSUPPORTED")
            continue
        match = subprocess.run(
            ["/usr/bin/fc-match", "--format=%{family}", "--", name],
            capture_output=True,
            timeout=5,
            check=True,
            env=environment,
        )  # nosec B603
        if name.casefold() not in {
            item.strip().casefold() for item in match.stdout.decode().split(",")
        }:
            gaps.append("WORD_FONT_SUBSTITUTED")
    response = subprocess.run(
        ["/usr/bin/fc-match", "-s", "--format=%{charset}\n"],
        capture_output=True,
        timeout=10,
        check=True,
        env=environment,
    )  # nosec B603
    ranges = []
    for token in response.stdout.decode().split():
        bounds = token.split("-")
        ranges.append((int(bounds[0], 16), int(bounds[-1], 16)))
    characters = {
        ord(c) for paragraph in inspection["paragraphs"] for c in paragraph if not c.isspace()
    }
    if any(not any(left <= value <= right for left, right in ranges) for value in characters):
        gaps.append("WORD_FONT_GLYPH_UNAVAILABLE")
    return list(dict.fromkeys(gaps))


def render(source: bytes, profile: dict):
    inspection = inspect_docx(source)
    if not inspection["conversion_eligible"]:
        return failure(source, profile, "WORD_PACKAGE_ACTIVE_CONTENT_BLOCKED")
    if profile["identity"].get("enabled") is False:
        return failure(source, profile, "WORD_RENDER_SANDBOX_UNAVAILABLE")
    try:
        actual = build_profile()
    except (DomainError, OSError, subprocess.SubprocessError):
        return failure(source, profile, "WORD_RENDER_SANDBOX_UNAVAILABLE")
    if actual["profile_digest"] != profile["profile_digest"]:
        return failure(source, profile, "WORD_RENDER_PROFILE_CHANGED")
    name = "twb-word-" + uuid4().hex
    isolation = None
    attempted = False
    outcome = None
    with tempfile.TemporaryDirectory(prefix="word-isolated-") as directory:
        path = Path(directory)
        file = path / "source.docx"
        file.write_bytes(source)
        file.chmod(0o444)  # Enclosing host directory remains private 0700.
        process = None
        try:
            _docker(_arguments(name, file, profile), directory)
            isolation = _inspect_isolation(name, directory, profile)
            attempted = True
            process = subprocess.Popen(
                [DOCKER, "--host", "unix:///var/run/docker.sock", "start", "--attach", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_host_environment(directory),
                start_new_session=True,
            )  # nosec B603
            encoded = _read_process(process, time.monotonic() + OPTIONS["timeout"])
            if process.returncode != 0:
                return failure(
                    source,
                    profile,
                    "WORD_CONVERSION_FAILED",
                    attempted=True,
                    retryable=True,
                    isolation=isolation,
                )
            header, data = encoded.split(b"\n", 1) if b"\n" in encoded else (encoded.strip(), b"")
            status = json.loads(header)
            if status.get("state") != "succeeded":
                return failure(
                    source,
                    profile,
                    "WORD_CONVERSION_TIMEOUT"
                    if status.get("error_code") == "WORD_CONVERSION_TIMEOUT"
                    else "WORD_CONVERSION_FAILED",
                    attempted=True,
                    retryable=True,
                    isolation=isolation,
                )
            if (
                not data.startswith(b"%PDF-")
                or not 0 < len(data) <= MAX_PDF
                or status.get("byte_size") != len(data)
            ):
                return failure(
                    source, profile, "WORD_PDF_INVALID", attempted=True, isolation=isolation
                )
            pdf = pypdfium2.PdfDocument(data)
            pieces = []
            try:
                if not 0 < len(pdf) <= 1000:
                    raise ValueError("WORD_PDF_PAGE_LIMIT")
                for index in range(len(pdf)):
                    page = pdf[index]
                    textpage = page.get_textpage()
                    try:
                        pieces.append(textpage.get_text_range())
                    finally:
                        textpage.close()
                        page.close()
                page_count = len(pdf)
            finally:
                pdf.close()
            gaps = _font_gaps(inspection, directory)
            source_text = "".join(inspection["paragraphs"])
            output_text = "".join(pieces)

            def normalize(text):
                return "".join(unicodedata.normalize("NFKC", text).split())

            if normalize(source_text) != normalize(output_text) or any(
                c in output_text for c in ("\ufffd", "\u25a1", "\u25a0")
            ):
                gaps.append("WORD_TEXT_CORRESPONDENCE_UNVERIFIED")
            if inspection["unsupported_layout_features"]:
                gaps.append("WORD_COMPLEX_LAYOUT_UNVERIFIED")
            if not source_text.strip():
                gaps.append("WORD_TEXT_SCOPE_EMPTY")
            gaps = list(dict.fromkeys(gaps))
            outcome = {
                "state": "succeeded",
                "coverage": "partial" if gaps else "complete",
                "conversion_attempted": True,
                "error_code": None,
                "retryable": False,
                "gaps": gaps,
                "chain": {
                    "source_docx_digest": sha(source),
                    "pdf_digest": sha(data),
                    "profile_digest": profile["profile_digest"],
                    **{
                        key: profile["identity"][key]
                        for key in ("renderer_digest", "fontset_digest", "options_digest")
                    },
                    "page_count": page_count,
                },
                "isolation": isolation,
                "pdf": data,
            }
            return outcome
        except (TimeoutError, subprocess.TimeoutExpired):
            return failure(
                source,
                profile,
                "WORD_CONVERSION_TIMEOUT",
                attempted=attempted,
                retryable=True,
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
                "WORD_CONVERSION_FAILED",
                attempted=attempted,
                retryable=True,
                isolation=isolation,
            )
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process and process.stdout:
                process.stdout.close()
            try:
                _docker(["rm", "--force", "--volumes", name], directory)
                if isolation is not None:
                    isolation["own_job_cleanup_confirmed"] = True
            except (OSError, subprocess.SubprocessError):
                if isolation is not None:
                    isolation["own_job_cleanup_confirmed"] = False
                if outcome is not None:
                    outcome["coverage"] = "partial"
                    outcome["gaps"].append("WORD_SANDBOX_CLEANUP_UNCONFIRMED")


def main():
    import argparse

    arguments = argparse.ArgumentParser()
    arguments.add_argument("--write-profile", type=Path, required=True)
    path = arguments.parse_args().write_profile
    if path.exists():
        raise SystemExit("Refusing to overwrite an installed renderer profile")
    value = build_profile()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    print({"profile_digest": value["profile_digest"], "image_id": IMAGE})


if __name__ == "__main__":
    main()
