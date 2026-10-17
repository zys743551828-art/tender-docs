"""Trusted container entrypoint. Only fixed argv, source path and bounded output.

This file is mounted read-only; it imports only the isolated system Python stdlib.
"""

import json
import os
import resource
import stat
import subprocess  # nosec B404
import sys

MAX_PDF = 50 * 1024 * 1024


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF, MAX_PDF))
    environment = {
        "HOME": "/tmp",  # nosec B108
        "TMPDIR": "/tmp",  # nosec B108
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "SAL_USE_VCLPLUGIN": "svp",
        "LD_LIBRARY_PATH": "/lo/program:/usr/lib/x86_64-linux-gnu",
    }
    argv = [
        "/lo/program/soffice.bin",
        "-env:UserInstallation=file:///tmp/word-profile",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        "/output",
        "/input/source.docx",
    ]
    try:
        result = subprocess.run(  # nosec B603
            argv,
            env=environment,
            cwd="/tmp",  # nosec B108
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=40,
            check=False,
        )  # nosec B603
        if result.returncode:
            raise ValueError("WORD_CONVERSION_FAILED")
        descriptor = os.open("/output/source.pdf", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("WORD_PDF_NOT_REGULAR_FILE")
            size = info.st_size
            if not 0 < size <= MAX_PDF:
                raise ValueError("WORD_PDF_OUTPUT_LIMIT")
            data = stream.read(MAX_PDF + 1)
        if len(data) != size or not data.startswith(b"%PDF-"):
            raise ValueError("WORD_PDF_INVALID")
        header = {"state": "succeeded", "byte_size": len(data)}
        sys.stdout.buffer.write(json.dumps(header).encode() + b"\n" + data)
    except subprocess.TimeoutExpired:
        sys.stdout.buffer.write(b'{"state":"failed","error_code":"WORD_CONVERSION_TIMEOUT"}\n')
    except (OSError, ValueError):
        sys.stdout.buffer.write(b'{"state":"failed","error_code":"WORD_CONVERSION_FAILED"}\n')


if __name__ == "__main__":
    main()
