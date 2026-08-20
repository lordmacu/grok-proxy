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


def test_listing_is_501_pending_a_live_asset_probe(monkeypatch):
    # grok_api_v2.FilesService/ListFiles is real but conversation-scoped (see
    # test_conversation_files.py); it does not back this account-wide route.
    # grok_api_v2.AssetRepository/ListAssetMetadata might, but whether a
    # chat-uploaded file shows up there as an asset is unmeasured -- see
    # capabilities.py's `files` docstring for the live probe that would settle it.
    with _client(monkeypatch) as c:
        r = c.get("/v1/files")
    assert r.status_code == 501
    assert "AssetRepository" in r.json()["detail"]


def test_get_by_id_is_501_pending_a_live_asset_probe(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.get("/v1/files/file-abc")
    assert r.status_code == 501
    assert "AssetRepository" in r.json()["detail"]


def test_delete_is_501_because_the_delete_rpc_is_unverified(monkeypatch):
    # grok_backend.delete_file always raises FileDeleteNotSupported: the only
    # delete-shaped RPC found in the decompiled APK, AssetRepository/DeleteAsset,
    # is keyed by asset_id, a namespace with no established link to a chat
    # file's file_metadata_id. The 501 names that -- same specificity as the
    # two GETs above, not a bare "not implemented".
    with _client(monkeypatch) as c:
        r = c.delete("/v1/files/file-abc")
    assert r.status_code == 501
    detail = r.json()["detail"]
    assert "DeleteAsset" in detail
    assert "asset_id" in detail


def test_the_contract_does_not_yet_claim_files():
    # Flips true only after the live probe capabilities.py describes: upload
    # a file via /grok/files, then call
    # grok_api_v2.AssetRepository/ListAssetMetadata and check whether the
    # returned file_id shows up as an asset_id.
    assert cap.effective(cap.SessionState(mode="account"))["files"] is False
