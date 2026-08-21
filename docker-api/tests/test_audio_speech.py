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


# ── text_to_speech's decode-and-accumulate loop ───────────────────────────────
#
# Every test above monkeypatches backend.text_to_speech wholesale, so none of
# them exercise the streaming parser inside it. These build real AudioChunk
# frames with the same _str_field/_bytes_field helpers the production code
# uses (so _decode_proto runs for real) and monkeypatch only _raw_stream, the
# network boundary.

def test_multiple_chunks_concatenate_in_order(monkeypatch):
    # The failure mode of a wrong loop is silent truncation, not an
    # exception -- this pins the exact concatenated bytes, not just a length,
    # and mixes an ASCII payload (decodes as _decode_proto's 'str' kind) with
    # a non-UTF-8 payload (decodes as 'raw') so both value shapes run.
    import grok_backend as gb

    chunk1 = gb._bytes_field(1, b"AAA-ascii")           # -> 'str' kind
    chunk2 = gb._bytes_field(1, b"\xff\xfe\x00\x01")    # -> 'raw' kind (invalid UTF-8)
    chunk3 = gb._bytes_field(1, b"CCC-ascii")           # -> 'str' kind

    monkeypatch.setattr(gb, "_raw_stream",
                        lambda *a, **k: iter([chunk1, chunk2, chunk3]))

    audio, content_type = gb.text_to_speech("hola", voice_id="ara")

    assert audio == b"AAA-ascii" + b"\xff\xfe\x00\x01" + b"CCC-ascii"
    assert content_type == "audio/mpeg"  # none of the chunks carry field 2


def test_content_type_comes_from_the_first_chunk_that_carries_one(monkeypatch):
    import grok_backend as gb

    chunk1 = gb._bytes_field(1, b"first")   # no content_type
    chunk2 = gb._bytes_field(1, b"second") + gb._str_field(2, "audio/wav")
    chunk3 = gb._bytes_field(1, b"third") + gb._str_field(2, "audio/should-not-win")

    monkeypatch.setattr(gb, "_raw_stream",
                        lambda *a, **k: iter([chunk1, chunk2, chunk3]))

    audio, content_type = gb.text_to_speech("hola", voice_id="ara")

    assert audio == b"firstsecondthird"
    assert content_type == "audio/wav"


def test_content_type_falls_back_to_audio_mpeg_without_a_chunk_carrying_one(monkeypatch):
    import grok_backend as gb

    chunk1 = gb._bytes_field(1, b"only-data")

    monkeypatch.setattr(gb, "_raw_stream", lambda *a, **k: iter([chunk1]))

    audio, content_type = gb.text_to_speech("hola", voice_id="ara")

    assert audio == b"only-data"
    assert content_type == "audio/mpeg"


def test_a_non_ascii_audio_chunk_survives_byte_for_byte(monkeypatch):
    # A chunk whose bytes happen to be valid UTF-8 comes back from
    # _decode_proto already decoded, so text_to_speech has to re-encode it.
    # encode('utf-8') is the exact inverse of that decode; encode('latin-1')
    # is not -- it silently shrinks b"\xc3\xa9AA" (4 bytes) to b"\xe9AA"
    # (3 bytes) and raises outright on anything above U+00FF, which is a
    # truncated mp3 or a 502 rather than an audible failure. Both the exact
    # bytes and the exact length are asserted: silent shortening is the
    # failure mode.
    import grok_backend as gb

    latin = "é".encode("utf-8")          # b"\xc3\xa9"  -- lossy under latin-1
    above = "€".encode("utf-8")          # b"\xe2\x82\xac" -- raises under latin-1

    chunk1 = gb._bytes_field(1, b"AAA-ascii")
    chunk2 = gb._bytes_field(1, latin + b"AA")
    chunk3 = gb._bytes_field(1, above)
    chunk4 = gb._bytes_field(1, b"\xff\xfe\x00\x01")   # -> 'raw' kind

    monkeypatch.setattr(gb, "_raw_stream",
                        lambda *a, **k: iter([chunk1, chunk2, chunk3, chunk4]))

    audio, content_type = gb.text_to_speech("hola", voice_id="ara")

    expected = b"AAA-ascii" + latin + b"AA" + above + b"\xff\xfe\x00\x01"
    assert audio == expected
    assert len(audio) == 9 + 4 + 3 + 4 == 20
    assert content_type == "audio/mpeg"


# ── Voz desconocida ───────────────────────────────────────────────────────────

def _client_rejecting_voices(monkeypatch, accept=""):
    """Un backend que sólo acepta `accept` y responde NOT_FOUND al resto.

    Reproduce lo que hace grok de verdad: `voice='alloy'` -- el valor por
    defecto de la API de OpenAI, y por lo tanto lo primero que manda cualquier
    cliente -- vuelve como gRPC NOT_FOUND ("Voice 'alloy' not found").
    """
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    calls = []

    def tts(text, voice_id=""):
        calls.append(voice_id)
        if voice_id != accept:
            raise RuntimeError(
                f"<_MultiThreadedRendezvous ... details = \"Voice '{voice_id}' not found\"")
        return (MP3, "audio/mpeg")

    monkeypatch.setattr(main.backend, "text_to_speech", tts)
    return TestClient(main.app), calls


def test_an_unknown_voice_falls_back_to_the_default(monkeypatch):
    """No es un 400: el catálogo de este backend viene vacío, así que un 400 no
    podría decir qué voces son válidas — sería un callejón sin salida."""
    client, calls = _client_rejecting_voices(monkeypatch)
    with client as c:
        r = c.post("/v1/audio/speech", json={"input": "hola", "voice": "alloy"})
    assert r.status_code == 200
    assert r.content == MP3
    assert calls == ["alloy", ""]


def test_the_fallback_is_reported_never_hidden(monkeypatch):
    """Sustituir la voz en silencio sería mentir sobre lo que se devolvió."""
    client, _ = _client_rejecting_voices(monkeypatch)
    with client as c:
        r = c.post("/v1/audio/speech", json={"input": "hola", "voice": "alloy"})
    assert r.headers["X-Voice-Fallback"] == "alloy"
    assert r.headers["X-Voice-Used"] == "default"


def test_a_valid_voice_is_not_retried(monkeypatch):
    client, calls = _client_rejecting_voices(monkeypatch, accept="Ara")
    with client as c:
        r = c.post("/v1/audio/speech", json={"input": "hola", "voice": "Ara"})
    assert r.status_code == 200
    assert calls == ["Ara"]
    assert "X-Voice-Fallback" not in r.headers


def test_a_real_backend_failure_is_still_502(monkeypatch):
    """El fallback es sólo para NOT_FOUND: una caída real sigue siendo 502."""
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")

    def boom(*a, **k):
        raise RuntimeError("UNAVAILABLE: backend is down")

    monkeypatch.setattr(main.backend, "text_to_speech", boom)
    with TestClient(main.app) as c:
        r = c.post("/v1/audio/speech", json={"input": "hola", "voice": "alloy"})
    assert r.status_code == 502


def test_a_failing_retry_is_502_not_a_loop(monkeypatch):
    monkeypatch.setattr(cap, "snapshot",
                        lambda: cap.SessionState(mode="account"))
    monkeypatch.setattr(main, "API_KEY", "")
    calls = []

    def always_not_found(text, voice_id=""):
        calls.append(voice_id)
        raise RuntimeError(f"Voice '{voice_id}' not found")

    monkeypatch.setattr(main.backend, "text_to_speech", always_not_found)
    with TestClient(main.app) as c:
        r = c.post("/v1/audio/speech", json={"input": "hola", "voice": "alloy"})
    assert r.status_code == 502
    assert calls == ["alloy", ""]
