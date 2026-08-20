import base64
import io

from fastapi.testclient import TestClient

import capabilities as cap
import main


def _client(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main.backend, "speech_to_text",
                        lambda *a, **k: "hola que tal")
    return TestClient(main.app)


def test_transcription_returns_the_openai_shape(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.post("/v1/audio/transcriptions",
                   files={"file": ("a.mp3", io.BytesIO(b"\x00\x01"), "audio/mpeg")})
    assert r.status_code == 200
    assert r.json() == {"text": "hola que tal"}


def test_response_format_text_returns_plain_text(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.post("/v1/audio/transcriptions",
                   files={"file": ("a.mp3", io.BytesIO(b"\x00\x01"), "audio/mpeg")},
                   data={"response_format": "text"})
    assert r.status_code == 200
    assert r.text.strip() == "hola que tal"


def test_the_format_comes_from_the_filename(monkeypatch):
    seen = {}

    def fake(audio, audio_format="mp3", **k):
        seen["format"] = audio_format
        return "ok"

    with _client(monkeypatch) as c:
        monkeypatch.setattr(main.backend, "speech_to_text", fake)
        c.post("/v1/audio/transcriptions",
               files={"file": ("clip.wav", io.BytesIO(b"\x00"), "audio/wav")})
    assert seen["format"] == "wav"


def test_the_request_frame_carries_the_recovered_tags():
    import grok_backend as gb
    frame = gb.build_stt_request(b"\x00\x01", "mp3")
    fields = gb._decode_proto(frame)
    assert gb._first_str(fields, 1) == base64.b64encode(b"\x00\x01").decode()
    assert gb._first_str(fields, 2) == "mp3"


def test_the_contract_now_claims_audio_transcription():
    assert cap.effective(cap.SessionState(mode="account"))["audio_transcription"] is True


# ── speech_to_text's wire-level parse ──────────────────────────────────────────
#
# Every test above monkeypatches backend.speech_to_text wholesale, so none of
# them exercise the parser inside it. This builds a real
# SpeechToTextGenerateResponse frame with the same _str_field helper the
# production code uses (so _decode_proto runs for real) and monkeypatches only
# _raw_unary, the network boundary -- pinning the actual wire-level parse
# rather than a hand-rolled dict, per the lesson from Task 8's TTS review.

def test_speech_to_text_extracts_the_transcript_from_a_real_frame(monkeypatch):
    import grok_backend as gb

    response_frame = gb._str_field(1, "the recovered transcript")
    monkeypatch.setattr(gb, "_raw_unary", lambda *a, **k: response_frame)

    text = gb.speech_to_text(b"\x00\x01", "mp3")

    assert text == "the recovered transcript"
