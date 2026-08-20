from fastapi.testclient import TestClient

import capabilities as cap
import main

MP3 = b"ID3\x03fake-audio-bytes"


def _client(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main.backend, "text_to_speech",
                        lambda *a, **k: (MP3, "audio/mpeg"))
    return TestClient(main.app)


def test_speech_returns_raw_audio_bytes(monkeypatch):
    # The contract promises bytes, not a JSON envelope: every OpenAI client
    # writes this response body straight to a file.
    with _client(monkeypatch) as c:
        r = c.post("/v1/audio/speech", json={"input": "hola"})
    assert r.status_code == 200
    assert r.content == MP3
    assert r.headers["content-type"].startswith("audio/mpeg")


def test_an_empty_input_is_400(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.post("/v1/audio/speech", json={"input": "   "})
    assert r.status_code == 400


def test_the_voice_is_passed_through(monkeypatch):
    seen = {}

    def fake(text, voice_id=None, **k):
        seen["voice"] = voice_id
        return MP3, "audio/mpeg"

    with _client(monkeypatch) as c:
        monkeypatch.setattr(main.backend, "text_to_speech", fake)
        c.post("/v1/audio/speech", json={"input": "hola", "voice": "ara"})
    assert seen["voice"] == "ara"


def test_the_contract_now_claims_audio_speech():
    assert cap.effective(cap.SessionState(mode="account"))["audio_speech"] is True


def test_the_request_frame_carries_the_recovered_tags():
    import grok_backend as gb
    frame = gb.build_tts_request("hola", voice_id="ara", language="es")
    fields = gb._decode_proto(frame)
    assert gb._first_str(fields, 1) == "hola"     # text
    assert gb._first_str(fields, 2) == "ara"      # voice_id
    assert gb._first_str(fields, 3) == "es"       # language
