import hashlib

import pytest
from fastapi.testclient import TestClient

from packages.parsing import server
from packages.parsing.http_port import ParsingHttp, ParsingUnavailable
from tests.test_parsing_local import pdf_bytes

TOKEN = "synthetic-parser-token-for-http-contract-tests-0001"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PARSING_RUNTIME_TOKEN", TOKEN)
    with TestClient(server.app) as client:
        yield client


def headers(data):
    return {
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/pdf",
        "X-Source-Sha256": hashlib.sha256(data).hexdigest(),
    }


def test_real_parser_http_auth_digest_and_bytes(client):
    assert client.get("/identity").status_code == 401
    data = pdf_bytes(lambda c: c.drawString(30, 300, "Evidence 120 days"))
    response = client.post("/parse", content=data, headers=headers(data))
    assert response.status_code == 200
    assert "Evidence 120 days" in response.json()["pages"][0]["text"]
    assert response.headers["cache-control"] == "no-store"
    wrong = headers(data)
    wrong["X-Source-Sha256"] = "0" * 64
    assert (
        client.post("/parse", content=data, headers=wrong).json()["code"] == "PDF_DIGEST_MISMATCH"
    )
    assert (
        client.post(
            "/parse", content=data, headers={"Authorization": "Bearer " + TOKEN}
        ).status_code
        == 422
    )


def test_runtime_busy_never_runs_parallel_native_parsers(client):
    data = pdf_bytes(lambda c: c.drawString(30, 300, "Resource test"))
    server.lock.acquire()
    try:
        assert client.post("/parse", content=data, headers=headers(data)).status_code == 429
    finally:
        server.lock.release()


def test_port_forbids_document_or_token_forwarding(monkeypatch):
    monkeypatch.setenv("PARSING_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("PARSING_RUNTIME_URL", "https://example.com/parse")
    with pytest.raises(ParsingUnavailable, match="PARSING_RUNTIME_CONFIG_INVALID"):
        ParsingHttp()


def test_port_validates_result_geometry_and_text(monkeypatch):
    monkeypatch.setenv("PARSING_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("PARSING_RUNTIME_URL", "http://127.0.0.1:8812")
    data = pdf_bytes(lambda c: c.drawString(30, 300, "Evidence geometry"))
    parser = server.LocalParser()
    result = parser.parse(data)
    metadata = {
        "protocol_version": 1,
        "identity": parser.identity,
        "parser_digest": parser.parser_digest,
        "config_digest": parser.config_digest,
    }
    monkeypatch.setattr(
        ParsingHttp,
        "_call",
        lambda self, method, path, data=None: metadata if method == "GET" else result,
    )
    port = ParsingHttp()
    assert port.parse(data)["coverage"] == "usable"
    result["pages"][0]["blocks"][0]["bbox"] = [0, 0, 99999, 5]
    with pytest.raises(ParsingUnavailable, match="PARSING_RESULT_INVALID"):
        port.parse(data)
