import io

from fastapi.testclient import TestClient

import capabilities as cap
import main


def _anon(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="anonymous"))
    monkeypatch.setattr(main, "API_KEY", "")
    return TestClient(main.app)


def test_image_generation_without_a_session_is_501(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.post("/v1/images/generations", json={"prompt": "a cat"})
    assert r.status_code == 501


def test_speech_without_a_session_is_501(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.post("/v1/audio/speech", json={"input": "hola"})
    assert r.status_code == 501


def test_transcription_without_a_session_is_501(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.post("/v1/audio/transcriptions",
                   files={"file": ("a.mp3", io.BytesIO(b"\x00"), "audio/mpeg")})
    assert r.status_code == 501


def test_files_without_a_session_is_501(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.get("/v1/files")
    assert r.status_code == 501


def test_conversations_without_a_session_is_501(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.get("/v1/conversations")
    assert r.status_code == 501


def test_the_501_body_names_the_capability(monkeypatch):
    with _anon(monkeypatch) as c:
        r = c.post("/v1/audio/speech", json={"input": "hola"})
    assert "audio_speech" in r.text


def test_models_and_health_are_never_gated(monkeypatch):
    with _anon(monkeypatch) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/v1/models").status_code == 200
