"""限量原生OSS探针：只写/读/删本次新建合成公式对象，不操作bucket或既有对象。"""

import json
import os
from pathlib import Path

import pytest
from test_pricing_integration import post_scenario, register, seed

from packages.domain import storage
from packages.domain.runtime import connection

pytest_plugins = ["test_pricing_integration"]

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PRICING_NATIVE_OSS") != "1", reason="仅显式授权的独立命名空间原生OSS探针"
)


def test_native_oss_formula_roundtrip_has_no_api_transaction_and_only_own_object_cleanup(
    client, monkeypatch
):
    assert os.environ.get("OBJECT_STORE_BACKEND") == "oss"
    native = storage.client()
    own_keys = []
    observations = []

    def no_idle_transaction():
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                ).fetchone()["n"]
                == 0
            )
        observations.append("no_idle_transaction")

    def put(key, raw, media):
        no_idle_transaction()
        assert not own_keys and key.startswith("pricing/formulas/") and len(raw) <= 4 * 1024 * 1024
        own_keys.append(key)
        return native.put_object(Bucket=storage.bucket(), Key=key, Body=raw, ContentType=media)

    def get(key):
        no_idle_transaction()
        assert key in own_keys
        body = native.get_object(Bucket=storage.bucket(), Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    monkeypatch.setattr(storage, "put", put)
    monkeypatch.setattr(storage, "get", get)
    web, actor, _, _ = client
    deleted = 0
    try:
        item = seed(actor)
        formula, _ = register(client, item)
        fetched = web.get(
            f"/api/v1/bids/{item['bid']}/pricing-formulas/" + formula.json()["formula_version_id"]
        )
        assert fetched.status_code == 200 and fetched.json() == formula.json()
        simulated = post_scenario(client, item, formula)
        assert simulated.status_code == 201 and simulated.json()["price_score"] == "25.00"
        assert len(observations) >= 3
    finally:
        for key in own_keys:
            native.delete_object(Bucket=storage.bucket(), Key=key)
            deleted += 1
    record = {
        "provider": "native_OSS_V4",
        "own_synthetic_formula_objects_created": len(own_keys),
        "own_synthetic_formula_objects_deleted": deleted,
        "existing_objects_or_bucket_settings_modified": False,
        "actual_nonowner_pg": True,
        "actual_session_csrf_api": True,
        "no_idle_transaction_observations": len(observations),
        "external_paid_model_calls": 0,
        "kafka_processes_started": 0,
        "real_business_materials": 0,
        "ephemeral_synthetic_object_cleanup": True,
    }
    destination = os.environ.get("PRICING_NATIVE_PROBE_RESULT")
    if destination:
        Path(destination).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
