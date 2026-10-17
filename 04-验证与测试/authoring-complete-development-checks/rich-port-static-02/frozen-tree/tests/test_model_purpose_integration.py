"""用途身份最小增量；真实非owner账本、离线显式传输，0收费。"""

import os
from uuid import uuid4

import httpx
import pytest
from test_model_ledger_integration import SCHEMA, executions, install_transport

from packages.domain import model_calls, storage
from packages.domain.providers.deepseek import DeepSeekClient, ProviderError
from packages.domain.runtime import DomainError, connection, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_PURPOSE_INTEGRATION") != "1", reason="需要专用真实PG"
)


def arguments():
    return {
        "logical_key": "purpose-check",
        "messages": [{"role": "user", "content": "Return JSON"}],
        "json_schema": SCHEMA,
        "max_tokens": 32,
    }


@pytest.mark.parametrize("bad", ["score", "generic", "", None, 42, ["assistant"]])
def test_invalid_purpose_rejected_before_client_or_reservation(monkeypatch, bad):
    def forbidden(*args, **kwargs):
        raise AssertionError("非法用途不得构建client或预留")

    monkeypatch.setattr(DeepSeekClient, "from_env", forbidden)
    monkeypatch.setattr(model_calls, "reserve", forbidden)
    with pytest.raises(DomainError) as error:
        model_calls.generate({}, {}, **arguments(), purpose=bad)
    assert error.value.code == "MODEL_PURPOSE_NOT_ALLOWED"


def test_default_profile_identity_reuse_and_cross_purpose_cache_isolation(monkeypatch):
    seen, objects = [], {}

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "purpose-" + uuid4().hex,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
            },
        )

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    with executions(3) as items:
        message, lease = items[0]
        first, identity = model_calls.generate(message, lease, **arguments())
        again, same = model_calls.generate(message, lease, **arguments(), purpose="core_review")
        assert first == again and identity == same and len(seen) == 1
        with connection(True) as c:
            old_profile = c.execute(
                "SELECT profile_digest FROM model_calls WHERE id=%s", (identity,)
            ).fetchone()["profile_digest"]
        assert old_profile == sha(
            {
                "provider": "deepseek",
                "model": "deepseek-flash",
                "port": "generation/1",
                "purpose": "core_review",
                "formal_scoring_eligible": False,
            }
        )
        with pytest.raises(DomainError) as error:
            model_calls.generate(message, lease, **arguments(), purpose="assistant")
        assert error.value.code == "MODEL_CALL_IDENTITY_CONFLICT" and len(seen) == 1
        ids = [identity]
        for (other, other_lease), purpose in zip(
            items[1:], ["assistant", "authoring"], strict=True
        ):
            _, call = model_calls.generate(other, other_lease, **arguments(), purpose=purpose)
            ids.append(call)
        with connection(True) as c:
            profiles = c.execute(
                "SELECT DISTINCT profile_digest FROM model_calls WHERE id=ANY(%s::uuid[])", (ids,)
            ).fetchall()
        assert len(profiles) == 3 and len(seen) == 3


@pytest.mark.parametrize("purpose", ["assistant", "authoring"])
def test_unknown_for_new_purpose_remains_no_resend(monkeypatch, purpose):
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout("synthetic")

    install_transport(monkeypatch, handler)
    with executions(2) as ((message, lease), (other, other_lease)):
        with pytest.raises(ProviderError):
            model_calls.generate(message, lease, **arguments(), purpose=purpose)
        for item, token in [(message, lease), (other, other_lease)]:
            with pytest.raises(DomainError) as error:
                model_calls.generate(item, token, **arguments(), purpose=purpose)
            assert error.value.code == "MODEL_CALL_REQUIRES_RECONCILIATION"
        assert len(seen) == 1
