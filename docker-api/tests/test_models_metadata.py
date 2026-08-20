from fastapi.testclient import TestClient

import capabilities as cap
import main

PER_MODEL = {"tools", "vision", "images"}
IMAGINE = {"imagine-agent-mode", "imagine-agent-mode-dev",
           "imagine-agent-mode-grok-4-5"}


def _models(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    with TestClient(main.app) as client:
        r = client.get("/v1/models")
    assert r.status_code == 200
    return r.json()["data"]


def test_every_model_carries_per_model_capabilities(monkeypatch):
    for m in _models(monkeypatch):
        assert set(m["capabilities"]) == PER_MODEL
        assert all(isinstance(v, bool) for v in m["capabilities"].values())


def test_only_the_imagine_family_claims_images(monkeypatch):
    models = _models(monkeypatch)
    drawing = {m["id"] for m in models if m["capabilities"]["images"]}
    assert drawing == IMAGINE & {m["id"] for m in models}


def test_the_imagine_family_claims_neither_tools_nor_vision(monkeypatch):
    # This is what retires the three hand-written exceptions in the gateway's
    # providers.yaml. It has to be exact, not approximately right.
    for m in _models(monkeypatch):
        if m["id"] in IMAGINE:
            assert m["capabilities"]["tools"] is False
            assert m["capabilities"]["vision"] is False


def test_the_chat_models_claim_tools_and_vision(monkeypatch):
    for m in _models(monkeypatch):
        if m["id"] not in IMAGINE:
            assert m["capabilities"]["tools"] is True
            assert m["capabilities"]["vision"] is True


def test_no_model_invents_a_context_window(monkeypatch):
    # grok publishes no context figure anywhere. Omitting the key lets the
    # gateway fall back to its declared floor; inventing one would be the
    # 128000-against-a-real-52815 mistake again.
    for m in _models(monkeypatch):
        assert "context_window" not in m or m["context_window"] is None
