"""私有精确key孤儿候选登记；不是清理授权，不记录真实provider UploadId。"""

import json
import os
from pathlib import Path

from packages.domain.runtime import now


def initialization_candidate(row, phase):
    directory = Path(__file__).resolve().parents[2] / ".local"
    directory.mkdir(mode=0o700, exist_ok=True)
    record = {
        "kind": "multipart_initialization_candidate",
        "phase": phase,
        "upload_id": str(row["id"]),
        "key": row["staging_key"],
        "prefix": os.environ.get("OBJECT_STORE_PREFIX", ""),
        "bucket": os.environ.get("OBJECT_STORE_BUCKET", ""),
        "created_at": now().isoformat(),
        "requires_database_reference_check": True,
        "provider_id_not_logged": True,
    }
    descriptor = os.open(
        directory / "multipart-initialization-candidates.jsonl",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (json.dumps(record, separators=(",", ":")) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
