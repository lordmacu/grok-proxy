from fastapi.testclient import TestClient

import capabilities as cap
import main

CONV = {"conversation_id": "c-1", "title": "hello",
        "create_time": "2026-08-20T00:00:00Z"}


def _client(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    return TestClient(main.app)


def test_listing_returns_a_list_object(monkeypatch):
    monkeypatch.setattr(main.backend, "list_conversations",
                        lambda **k: {"conversations": [CONV], "next_cursor": None})
    with _client(monkeypatch) as c:
        r = c.get("/v1/conversations")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "c-1"


def test_detail_is_addressable(monkeypatch):
    monkeypatch.setattr(main.backend, "get_conversation", lambda cid: CONV)
    with _client(monkeypatch) as c:
        r = c.get("/v1/conversations/c-1")
    assert r.status_code == 200
    assert r.json()["id"] == "c-1"


def test_an_unknown_conversation_is_404(monkeypatch):
    monkeypatch.setattr(main.backend, "get_conversation", lambda cid: {})
    with _client(monkeypatch) as c:
        r = c.get("/v1/conversations/nope")
    assert r.status_code == 404


def test_the_native_surface_still_works(monkeypatch):
    monkeypatch.setattr(main.backend, "list_conversations",
                        lambda **k: {"conversations": [CONV], "next_cursor": None})
    with _client(monkeypatch) as c:
        r = c.get("/grok/conversations")
    assert r.status_code == 200


def test_the_contract_now_claims_conversations():
    assert cap.effective(cap.SessionState(mode="account"))["conversations"] is True
