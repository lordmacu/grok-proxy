import io

from fastapi.testclient import TestClient

import capabilities as cap
import main

UPLOADED = {"file_id": "file-abc", "mime_type": "text/plain",
            "storage_path": "u/1", "created_at": "2026-08-20T00:00:00Z"}


def _client(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    return TestClient(main.app)


def test_upload_returns_the_openai_shape(monkeypatch):
    monkeypatch.setattr(main.backend, "upload_file", lambda *a, **k: UPLOADED)
    with _client(monkeypatch) as c:
        r = c.post("/v1/files", files={"file": ("a.txt", io.BytesIO(b"hi"),
                                                "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "file-abc"
    assert body["object"] == "file"
    assert body["purpose"] == "assistants"
    assert body["filename"] == "a.txt"
    assert isinstance(body["bytes"], int)


def test_listing_returns_a_list_object(monkeypatch):
    monkeypatch.setattr(main.backend, "list_files",
                        lambda limit=100: [dict(UPLOADED, filename="a.txt")])
    with _client(monkeypatch) as c:
        r = c.get("/v1/files")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "file-abc"


def test_a_single_file_is_addressable(monkeypatch):
    monkeypatch.setattr(main.backend, "list_files",
                        lambda limit=100: [dict(UPLOADED, filename="a.txt")])
    with _client(monkeypatch) as c:
        r = c.get("/v1/files/file-abc")
    assert r.status_code == 200
    assert r.json()["id"] == "file-abc"


def test_an_unknown_file_is_404(monkeypatch):
    monkeypatch.setattr(main.backend, "list_files", lambda limit=100: [])
    with _client(monkeypatch) as c:
        r = c.get("/v1/files/file-nope")
    assert r.status_code == 404


def test_delete_returns_the_openai_shape(monkeypatch):
    monkeypatch.setattr(main.backend, "delete_file", lambda fid: {"deleted": True})
    with _client(monkeypatch) as c:
        r = c.delete("/v1/files/file-abc")
    assert r.status_code == 200
    assert r.json() == {"id": "file-abc", "object": "file", "deleted": True}


def test_the_contract_now_claims_files():
    assert cap.effective(cap.SessionState(mode="account"))["files"] is True
