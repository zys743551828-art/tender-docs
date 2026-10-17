import pytest
@pytest.fixture(autouse=True)
def no_remote(monkeypatch):
 import requests,httpx,subprocess,psycopg
 def deny(*args,**kwargs): raise AssertionError("navigation independent forbids DB/network/subprocess")
 monkeypatch.setattr(requests.sessions.Session,"request",deny)
 monkeypatch.setattr(httpx.HTTPTransport,"handle_request",deny)
 monkeypatch.setattr(subprocess,"run",deny)
 monkeypatch.setattr(subprocess,"Popen",deny)
 monkeypatch.setattr(psycopg,"connect",deny)
