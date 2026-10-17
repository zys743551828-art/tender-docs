"""Offline runtime transport boundaries; no model quality or external connectivity claims."""

import httpx
import pytest

from packages.rag.http_port import DenseHttp, LexicalHttp, RuntimeUnavailable


def test_private_runtime_ignores_proxy_and_rechecks_method_identities(monkeypatch):
    monkeypatch.setenv("RAG_RUNTIME_TOKEN", "synthetic-local-runtime-token-32-characters")
    monkeypatch.setenv("RAG_RUNTIME_URL", "http://127.0.0.1:8810")
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic-proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "")
    identity = {
        "model_name": "BAAI/bge-m3",
        "model_revision": "1" * 40,
        "weights_digest": "2" * 64,
        "preprocessing_digest": "3" * 64,
    }
    dictionary = "4" * 64
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/identity":
            return httpx.Response(
                200,
                json={
                    "protocol_version": 1,
                    "dimensions": 1024,
                    "identity": identity,
                    "lexical_dictionary_digest": dictionary,
                },
            )
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"identity": identity, "offsets": [[0, 4]]})
        if request.url.path == "/lexical":
            return httpx.Response(200, json={"dictionary_digest": dictionary, "lexemes": "road"})
        return httpx.Response(200, json={"identity": identity, "vectors": [[1.0] + [0.0] * 1023]})

    client_class = httpx.Client

    def local_client(**kwargs):
        assert kwargs["trust_env"] is False
        return client_class(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", local_client)
    dense = DenseHttp()
    assert dense.offsets("road") == [(0, 4)]
    assert LexicalHttp(dense).lexemes("road") == "road"
    assert len(dense.embed(["road"])[0]) == 1024
    identity["preprocessing_digest"] = "5" * 64
    with pytest.raises(RuntimeUnavailable, match="PROFILE_CHANGED"):
        dense.offsets("road")
    assert len(requests) == 5


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid",
        "http://127.0.0.1:8810/path",
        "http://user@127.0.0.1:8810",
        "http://localhost:8810",
    ],
)
def test_runtime_target_rejected_before_sending_private_token(monkeypatch, url):
    monkeypatch.setenv("RAG_RUNTIME_TOKEN", "synthetic-private-token-over-32-characters")
    monkeypatch.setenv("RAG_RUNTIME_URL", url)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Must not send a token"))
    with pytest.raises(RuntimeUnavailable, match="CONFIG_INVALID"):
        DenseHttp()
