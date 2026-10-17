import pytest
@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import requests,httpx,subprocess
    from packages.domain import storage
    from packages.domain.providers.deepseek import DeepSeekClient
    def forbidden(*args,**kwargs):
        raise AssertionError("Composition independent check forbids external I/O")
    async def forbidden_async(*args,**kwargs):
        forbidden()
    monkeypatch.setattr(requests.sessions.Session,"request",forbidden)
    monkeypatch.setattr(httpx.HTTPTransport,"handle_request",forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,"handle_async_request",forbidden_async)
    monkeypatch.setattr(DeepSeekClient,"from_env",forbidden)
    monkeypatch.setattr(storage,"client",forbidden)
    monkeypatch.setattr(storage,"get",forbidden)
    monkeypatch.setattr(storage,"put",forbidden)
    monkeypatch.setattr(subprocess,"run",forbidden)
    monkeypatch.setattr(subprocess,"Popen",forbidden)
