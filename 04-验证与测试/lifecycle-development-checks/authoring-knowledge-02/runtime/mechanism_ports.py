"""Explicit no-network object/model ports for affected mechanism regressions."""
import pytest
from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient

@pytest.fixture(autouse=True)
def mechanism_ports(monkeypatch):
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    def forbidden(*args, **kwargs):
        raise AssertionError("Real external object/model ports forbidden")
    monkeypatch.setattr(storage, "client", forbidden)
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(forbidden))
