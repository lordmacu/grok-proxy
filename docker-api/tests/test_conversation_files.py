from fastapi.testclient import TestClient

import capabilities as cap
import main

ENTRY = {"file_id": "notes/a.txt", "filename": "a.txt",
         "mime_type": "text/plain", "storage_path": "notes/a.txt",
         "bytes": 2, "created_at": "2026-08-20T00:00:00Z"}


def _client(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    return TestClient(main.app)


def test_lists_a_conversations_files(monkeypatch):
    seen = {}

    def fake_list_files(conversation_id, path="", limit=100):
        seen["conversation_id"] = conversation_id
        seen["path"] = path
        seen["limit"] = limit
        return [ENTRY]

    monkeypatch.setattr(main.backend, "list_files", fake_list_files)
    with _client(monkeypatch) as c:
        r = c.get("/grok/conversations/conv-1/files")
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"] == "conv-1"
    assert body["count"] == 1
    assert body["files"][0]["file_id"] == "notes/a.txt"
    # backend.list_files is conversation-scoped, not a bare listing call
    assert seen["conversation_id"] == "conv-1"
    assert seen["path"] == ""
    assert seen["limit"] == 100


def test_forwards_the_optional_path_and_limit(monkeypatch):
    seen = {}

    def fake_list_files(conversation_id, path="", limit=100):
        seen["path"] = path
        seen["limit"] = limit
        return []

    monkeypatch.setattr(main.backend, "list_files", fake_list_files)
    with _client(monkeypatch) as c:
        r = c.get("/grok/conversations/conv-1/files",
                   params={"path": "subdir", "limit": 10})
    assert r.status_code == 200
    assert seen["path"] == "subdir"
    assert seen["limit"] == 10


def test_backend_errors_become_502(monkeypatch):
    def boom(conversation_id, path="", limit=100):
        raise RuntimeError("grpc unavailable")

    monkeypatch.setattr(main.backend, "list_files", boom)
    with _client(monkeypatch) as c:
        r = c.get("/grok/conversations/conv-1/files")
    assert r.status_code == 502
