"""Fresh reviewed RICH schema only: real restricted PG; no external I/O/model."""

import copy
import io
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import requests
import test_authoring_integration as legacy
import test_authoring_rich as rich_cases
from PIL import Image, PngImagePlugin

from packages.authoring import rich_assets, rich_images
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, assert_role, connection, sha
from packages.domain.work_access import begin

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_RICH_INTEGRATION") != "1",
    reason="Explicit fresh reviewed RICH nonowner profile required",
)


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("RICH mechanism fixture forbids real external HTTP/provider")

    async def forbidden_async(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async)
    monkeypatch.setattr(DeepSeekClient, "from_env", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)


@pytest.fixture
def images():
    actor, bid, draft, saved, section = legacy.draft.__wrapped__()
    # blobs de-duplicate globally across test runs. A fresh memory store cannot
    # truthfully serve an earlier fixture's key, so use truly distinct pixels.
    output = io.BytesIO()
    with Image.frombytes("RGB", (4, 4), uuid4().bytes * 3) as raster:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("fixture", "explicit synthetic original")
        raster.save(output, format="PNG", pnginfo=metadata)
    raw = output.getvalue()
    normalized = rich_images.normalize(raw, "image/png")
    objects = {}

    def obj(data, name):
        key = f"authoring-images/{bid}/{uuid4()}/{name}"
        objects[key] = data
        return {
            "digest": sha(data),
            "byte_size": len(data),
            "object_key": key,
            "media_type": "image/png",
        }

    return {
        "actor": actor,
        "bid": bid,
        "draft": draft,
        "section": section,
        "saved": saved,
        "original": obj(raw, "original"),
        "normalized": obj(normalized["bytes"], "normalized"),
        "proof": normalized["proof"],
        "objects": objects,
    }


def register(env, refs=None):
    key, body = (
        uuid4().hex,
        {"source_refs": refs or [], "normalization_proof_digest": sha(env["proof"])},
    )
    with connection() as c:
        cmd = begin(
            c, env["actor"], "register-rich-image-test", env["bid"], key, body, None, bid=env["bid"]
        )
        value = rich_assets.insert(
            c,
            env["bid"],
            env["actor"],
            "真实规范图片",
            "明确上传",
            refs or [],
            env["proof"],
            env["original"],
            env["normalized"],
        )
        receipt = cmd.finish(value, 201)
    return value, key, body, receipt


def test_real_nonowner_asset_roundtrip_worker_read_and_exact_memory_bytes(images):
    assert_role()
    assert_role(True)
    value, _, _, _ = register(images)
    identities = [(value["asset_id"], 1, value["id"])]
    with connection(True) as c:
        frozen = rich_assets.freeze_assets(c, images["bid"], identities, worker=True)
        assert len(frozen) == 1
        assert frozen[0]["asset_version_id"] == value["id"]
        for role in ["tender_worker", "tender_score_read", "tender_research_read"]:
            assert not c.execute(
                "SELECT has_table_privilege(%s,'design.authoring_image_versions','INSERT') AS ok",
                (role,),
            ).fetchone()["ok"]
    data = images["objects"][frozen[0]["normalized_blob"]["object_key"]]
    assert rich_images.verify_normalized(data, frozen[0]["normalization_proof"])


def test_withdraw_rejects_new_use_but_old_version_and_exact_receipt_survive(images):
    value, key, body, receipt = register(images)
    with connection() as c:
        withdrawn = rich_assets.withdraw(
            c, images["bid"], UUID(value["asset_id"]), images["actor"], value["etag"], "明确停用"
        )
        assert withdrawn["revision"] == 2 and withdrawn["state"] == "withdrawn"
    with connection() as c:
        old = rich_assets.version(c, images["bid"], UUID(value["asset_id"]), UUID(value["id"]))
        assert not old["is_current"] and old["normalization_proof"] == value["normalization_proof"]
        cmd = begin(
            c,
            images["actor"],
            "register-rich-image-test",
            images["bid"],
            key,
            body,
            None,
            bid=images["bid"],
        )
        assert cmd.replay == receipt
    with connection(True) as c, pytest.raises(DomainError) as error:
        rich_assets.freeze_assets(
            c, images["bid"], [(value["asset_id"], 1, value["id"])], worker=True
        )
    assert error.value.code == "AUTHORING_IMAGE_STALE"
    assert all(images["objects"].values())


def test_concurrent_withdraw_has_one_cas_winner(images):
    value, _, _, _ = register(images)
    barrier = Barrier(2)

    def withdraw(reason):
        barrier.wait()
        try:
            with connection() as c:
                return rich_assets.withdraw(
                    c,
                    images["bid"],
                    UUID(value["asset_id"]),
                    images["actor"],
                    value["etag"],
                    reason,
                )
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(withdraw, ["停用甲", "停用乙"]))
    assert sum(isinstance(v, dict) for v in values) == 1 and 412 in values
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM authoring_image_versions WHERE asset_id=%s",
                (UUID(value["asset_id"]),),
            ).fetchone()["n"]
            == 2
        )


def test_actual_worker_dml_is_denied_and_api_metadata_is_not_updatable(images):
    value, _, _, _ = register(images)
    with connection(True) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(
            "UPDATE authoring_image_assets SET head_revision=head_revision+1 WHERE id=%s",
            (UUID(value["asset_id"]),),
        )
    with connection(True) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("DELETE FROM authoring_image_versions WHERE id=%s", (UUID(value["id"]),))
    with connection() as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(
            "UPDATE authoring_image_assets SET title='不可篡改' WHERE id=%s",
            (UUID(value["asset_id"]),),
        )
    with connection() as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(
            "UPDATE authoring_image_versions SET state='withdrawn' WHERE id=%s",
            (UUID(value["id"]),),
        )


def test_cross_bid_and_wrong_version_identity_do_not_read_or_qualify(images):
    value, _, _, _ = register(images)
    with connection() as c, pytest.raises(DomainError):
        rich_assets.version(c, uuid4(), UUID(value["asset_id"]))
    with connection(True) as c, pytest.raises(DomainError):
        rich_assets.freeze_assets(
            c, images["bid"], [(value["asset_id"], 2, value["id"])], worker=True
        )
    with connection() as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute(
            "UPDATE authoring_image_assets SET head_revision=head_revision+2 WHERE id=%s",
            (UUID(value["asset_id"]),),
        )


def test_source_ancestor_change_blocks_new_image_use_and_history_stays(images):
    with connection() as c:
        ref, ancestor = legacy.create_authoritative_source(c, images["bid"], images["actor"])
    value, _, _, _ = register(images, [ref])
    with connection(True) as c:
        rich_assets.freeze_assets(
            c, images["bid"], [(value["asset_id"], 1, value["id"])], worker=True
        )
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
            (ancestor,),
        )
    with connection(True) as c, pytest.raises(DomainError) as error:
        rich_assets.freeze_assets(
            c, images["bid"], [(value["asset_id"], 1, value["id"])], worker=True
        )
    assert error.value.code == "AUTHORING_SOURCE_STALE"
    with connection() as c:
        old = rich_assets.version(c, images["bid"], UUID(value["asset_id"]), UUID(value["id"]))
        assert old["source_refs"] == [ref]


def test_final_guard_refuses_repeatable_read_connection(images):
    value, _, _, _ = register(images)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(DomainError):
            rich_assets.freeze_assets(c, images["bid"], [(value["asset_id"], 1, value["id"])])


def test_final_guard_refuses_autocommit_that_would_release_bid_lock(images):
    value, _, _, _ = register(images)
    with connection(True) as c:
        c.commit()
        c.autocommit = True
        with pytest.raises(DomainError) as error:
            rich_assets.freeze_assets(
                c, images["bid"], [(value["asset_id"], 1, value["id"])], worker=True
            )
        assert error.value.code == "AUTHORING_IMAGE_REQUIRES_TRANSACTION"


@pytest.fixture
def rich_api(images, monkeypatch):
    import hashlib
    from contextlib import contextmanager
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from apps.api.authoring_rich import router
    from apps.api.main import app
    from packages.authoring import rich_uploads
    from packages.domain import storage
    from packages.domain.runtime import now, secret_hash

    # Runtime-only registration in this process, never shared main source or 8806.
    if not any(route.path.endswith("/authoring-image-uploads/{upload_id}") for route in app.routes):
        app.include_router(router)
    token, csrf = uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                images["actor"],
                secret_hash(token),
                hashlib.sha256(csrf.encode()).hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    connections = []
    original_connection = rich_uploads.connection

    @contextmanager
    def observed(*args, **kwargs):
        with original_connection(*args, **kwargs) as c:
            connections.append(c)
            yield c

    def put(key, data, media):
        assert connections and all(c.closed for c in connections)
        images["objects"][key] = data

    class MemoryClient:
        def get_object(self, *, Bucket, Key):
            return {"Body": io.BytesIO(images["objects"][Key])}

    monkeypatch.setattr(rich_uploads, "connection", observed)
    monkeypatch.setattr(storage, "put", put)
    monkeypatch.setattr(storage, "get", images["objects"].__getitem__)
    monkeypatch.setattr(storage, "client", MemoryClient)
    headers = {
        "Cookie": "session=" + token,
        "Origin": os.environ["APP_ORIGIN"],
        "X-CSRF-Token": csrf,
        "Idempotency-Key": uuid4().hex,
    }
    with TestClient(app) as client:
        yield client, headers, f"/api/v1/bids/{images['bid']}/authoring-images"


def upload_body(images):
    import base64

    data = images["objects"][images["original"]["object_key"]]
    return {
        "title": "真实上传图片",
        "reason": "合成编制插图",
        "media_type": "image/png",
        "content_base64": base64.b64encode(data).decode(),
        "source_refs": [],
    }


def test_actual_image_http_csrf_upload_exact_replay_withdraw_and_old_bytes(images, rich_api):
    client, headers, path = rich_api
    body = upload_body(images)
    forbidden = client.post(path, headers=headers | {"X-CSRF-Token": "wrong"}, json=body)
    assert forbidden.status_code == 403
    created = client.post(path, headers=headers, json=body)
    assert created.status_code == 201, created.json()
    value = created.json()
    replay = client.post(path, headers=headers, json=body)
    assert (
        replay.status_code == 201
        and replay.json() == value
        and replay.headers["Idempotent-Replayed"] == "true"
    )
    upload_path = f"/api/v1/bids/{images['bid']}/authoring-image-uploads/{value['upload_id']}"
    status = client.get(upload_path, headers=headers)
    assert (
        status.status_code == 200
        and status.json()["state"] == "completed"
        and status.json()["automatic_retry"] is False
    )
    asset_path = path + "/" + value["asset_id"]
    stopped = client.post(
        asset_path + "/withdraw",
        headers=headers | {"Idempotency-Key": uuid4().hex, "If-Match": created.headers["ETag"]},
        json={"reason": "停用后保留历史"},
    )
    assert stopped.status_code == 200 and stopped.json()["state"] == "withdrawn"
    old_path = asset_path + "/versions/" + value["id"]
    old = client.get(old_path, headers=headers)
    assert old.status_code == 200 and old.json()["is_current"] is False
    data = client.get(old_path + "/bytes/original", headers=headers)
    assert (
        data.status_code == 200
        and data.content == images["objects"][images["original"]["object_key"]]
    )
    with connection(True) as c, pytest.raises(DomainError):
        rich_assets.freeze_assets(
            c, images["bid"], [(value["asset_id"], 1, value["id"])], worker=True
        )


def test_object_unknown_has_recoverable_record_and_no_automatic_retry(
    images, rich_api, monkeypatch
):
    from packages.domain import storage

    client, headers, path = rich_api
    calls = []
    real_put = storage.put

    def fail_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("explicit synthetic unknown")
        return real_put(*args)

    monkeypatch.setattr(storage, "put", fail_once)
    body = upload_body(images)
    failed = client.post(path, headers=headers, json=body)
    assert failed.status_code == 503 and len(calls) == 1
    upload_id = failed.json()["details"]["upload_id"]
    status_path = f"/api/v1/bids/{images['bid']}/authoring-image-uploads/{upload_id}"
    unknown = client.get(status_path, headers=headers)
    assert unknown.status_code == 200 and unknown.json()["state"] == "unconfirmed"
    assert unknown.json()["automatic_retry"] is False
    repeated = client.post(path, headers=headers, json=body)
    assert repeated.status_code == 201 and len(calls) == 3
    assert client.get(status_path, headers=headers).json()["state"] == "completed"
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM authoring_image_assets WHERE bid_id=%s", (images["bid"],)
            ).fetchone()["n"]
            == 1
        )


def rich_change(images, value, *, revision=1):
    from packages.authoring.rich_content import SCHEMA_DIGEST

    content = copy.deepcopy(images["section"]["content"])
    content["content"] = [
        rich_cases.table(
            rich_cases.row(rich_cases.cell(2, 2), rich_cases.cell()),
            rich_cases.row(rich_cases.cell()),
        ),
        {
            "type": "image",
            "attrs": {
                "id": uuid4().hex,
                "asset_id": value["asset_id"],
                "asset_revision": value["revision"],
                "asset_version_id": value["id"],
                "width_twips": 2400,
                "height_twips": 2400,
                "alt": "有真实资产的图片",
                "caption": "明确插图",
            },
        },
    ]
    return {
        "base_draft_revision": revision,
        "changes": [
            {
                "section_id": str(images["section"]["section_id"]),
                "base_section_revision": revision,
                "schema_digest": SCHEMA_DIGEST,
                "content": content,
            }
        ],
    }


def test_rich_save_freezes_real_asset_versions_and_old_snapshot_is_byte_stable(images):
    from packages.authoring.repository import save_draft, snapshot_content
    from packages.domain.runtime import etag

    value, _, _, _ = register(images)
    body = rich_change(images, value)
    with connection() as c:
        original = snapshot_content(c, images["bid"], UUID(images["saved"]["snapshot_id"]))
        saved = save_draft(
            c,
            images["bid"],
            images["draft"],
            images["actor"],
            body,
            etag("draft", images["draft"], 1),
        )
        frozen = snapshot_content(c, images["bid"], UUID(saved["snapshot_id"]))
        assert frozen["sections"][0] == body["changes"][0]["content"]
        assert frozen["authoring_images"]["items"][0]["asset_version_id"] == value["id"]
        assert (
            frozen["authoring_images"]["items"][0]["normalization_proof"]
            == value["normalization_proof"]
        )
        row = c.execute(
            "SELECT schema_digest FROM section_revisions WHERE section_id=%s AND revision=2",
            (images["section"]["section_id"],),
        ).fetchone()
        assert row["schema_digest"] == body["changes"][0]["schema_digest"]
    with connection() as c:
        rich_assets.withdraw(
            c,
            images["bid"],
            UUID(value["asset_id"]),
            images["actor"],
            value["etag"],
            "撤回已在稿图片",
        )
    later = rich_change(images, value, revision=2)
    with connection() as c, pytest.raises(DomainError) as error:
        save_draft(
            c,
            images["bid"],
            images["draft"],
            images["actor"],
            later,
            etag("draft", images["draft"], 2),
        )
    assert error.value.code == "AUTHORING_IMAGE_STALE"
    with connection() as c:
        assert snapshot_content(c, images["bid"], UUID(saved["snapshot_id"])) == frozen
        assert snapshot_content(c, images["bid"], UUID(images["saved"]["snapshot_id"])) == original
        assert (
            c.execute("SELECT revision FROM drafts WHERE id=%s", (images["draft"],)).fetchone()[
                "revision"
            ]
            == 2
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (images["draft"],)
            ).fetchone()["n"]
            == 2
        )


def test_legacy_digest_does_not_admit_rich_and_overwide_save_rolls_back(images):
    from packages.authoring.content import SCHEMA_DIGEST
    from packages.authoring.repository import save_draft
    from packages.domain.runtime import etag

    value, _, _, _ = register(images)
    body = rich_change(images, value)
    bad = copy.deepcopy(body)
    bad["changes"][0]["schema_digest"] = SCHEMA_DIGEST
    with connection() as c, pytest.raises(DomainError):
        save_draft(
            c,
            images["bid"],
            images["draft"],
            images["actor"],
            bad,
            etag("draft", images["draft"], 1),
        )
    body["changes"][0]["content"]["content"][0]["attrs"]["column_widths_twips"] = [5000, 5000, 5000]
    with connection() as c, pytest.raises(DomainError) as error:
        save_draft(
            c,
            images["bid"],
            images["draft"],
            images["actor"],
            body,
            etag("draft", images["draft"], 1),
        )
    assert error.value.code == "AUTHORING_TABLE_TOO_WIDE"
    with connection() as c:
        assert (
            c.execute("SELECT revision FROM drafts WHERE id=%s", (images["draft"],)).fetchone()[
                "revision"
            ]
            == 1
        )
