"""Pins the observable behaviour of stream_chat's grok-plugins-* download-link
fallback, which today reads the ListFiles RPC through the private
_list_files (a second, hand-rolled parser for the same RPC that
grok_backend.list_files also wraps -- see docker-api/grok_backend.py).

This test is written and passed BEFORE consolidating that call site onto
list_files, specifically so the consolidation can be verified not to change
this fallback's output. It must keep passing after the refactor with no
edits to the test itself.
"""
import grok_backend


class _FakeChannel:
    """Stub gRPC channel serving one canned ListFiles response, regardless
    of the outgoing request bytes -- request-shape correctness was already
    verified against the decompiled proto in grok_backend.list_files's
    docstring; this test only pins what stream_chat does with the reply."""

    def __init__(self, list_files_response: bytes):
        self._list_files_response = list_files_response

    def unary_unary(self, path, request_serializer=None, response_deserializer=None):
        assert path == "/grok_api_v2.FilesService/ListFiles"
        def _call(req_bytes, metadata=None, timeout=None):
            return self._list_files_response
        return _call


def _list_files_response(name: str, path: str, size: int, mime_type: str) -> bytes:
    """Hand-encodes one grok_api_v2.File entry inside a ListFilesResponse,
    matching the real proto (name=1, path=2, size=4, mime_type=5)."""
    entry = (grok_backend._str_field(1, name)
             + grok_backend._str_field(2, path)
             + grok_backend._int_field(4, size)
             + grok_backend._str_field(5, mime_type))
    return grok_backend._nested_field(1, entry)


def _chat_chunks(conv_id: str, token: str):
    """Two raw CreateConversationAndRespond chunks: one carrying the new
    conversation_id (nested field 2 -> field 1), one carrying an assistant
    text token (nested field 1 -> field 2) -- the minimum stream_chat needs
    to reach its post-stream, grok-plugins-*, no-renders branch."""
    yield grok_backend._nested_field(2, grok_backend._str_field(1, conv_id))
    yield grok_backend._nested_field(1, grok_backend._str_field(2, token))


def test_plugin_file_fallback_lists_download_links(monkeypatch):
    raw = _list_files_response("report.pdf", "conv-1/report.pdf", 2048,
                               "application/pdf")
    monkeypatch.setattr(grok_backend, "get_channel", lambda: _FakeChannel(raw))
    monkeypatch.setattr(grok_backend, "_make_meta", lambda: [])
    monkeypatch.setattr(grok_backend, "_stream_raw_chat",
                        lambda req_bytes, timeout=120: _chat_chunks("conv-1", "hi"))
    monkeypatch.setattr(grok_backend, "_resolve_file_url",
                        lambda conv_id, file_path, timeout=15:
                            f"https://cdn.example/{file_path}")

    result = "".join(grok_backend.stream_chat("prompt", "grok-plugins-test"))

    assert "[Descargar: **report.pdf**]" in result
    assert "https://cdn.example/report.pdf" in result
    assert "2.0 KB" in result
    assert "application/pdf" in result


class _FailingChannel:
    """Simulates a ListFiles RPC that errors out."""

    def unary_unary(self, path, request_serializer=None, response_deserializer=None):
        def _call(req_bytes, metadata=None, timeout=None):
            raise RuntimeError("simulated ListFiles failure")
        return _call


def test_plugin_file_fallback_survives_a_listfiles_failure(monkeypatch):
    """The old _list_files swallowed every exception (`except Exception:
    return []`), so a ListFiles hiccup never broke an otherwise-successful
    chat response. list_files itself must NOT swallow -- other callers
    (GET /grok/conversations/{conv_id}/files) need the exception to surface
    as a 502 -- so this call site has to swallow it locally instead."""
    monkeypatch.setattr(grok_backend, "get_channel", lambda: _FailingChannel())
    monkeypatch.setattr(grok_backend, "_make_meta", lambda: [])
    monkeypatch.setattr(grok_backend, "_stream_raw_chat",
                        lambda req_bytes, timeout=120: _chat_chunks("conv-1", "hi"))
    monkeypatch.setattr(grok_backend, "_resolve_file_url",
                        lambda conv_id, file_path, timeout=15: "https://cdn.example/x")

    # Must not raise -- a failed download-link lookup should not take down
    # an otherwise-complete chat response.
    result = "".join(grok_backend.stream_chat("prompt", "grok-plugins-test"))
    assert "hi" in result
    assert "Descargar" not in result


def test_plugin_file_fallback_preserves_a_non_ascii_filename(monkeypatch):
    """A ListFiles entry whose bytes are all valid UTF-8 comes back from
    _decode_proto as a decoded ('str', ...) value, so list_files has to
    re-encode it to recover the nested entry. The only correct inverse of
    the decode('utf-8') that produced it is encode('utf-8'): latin-1
    mangles U+0080-U+00FF and raises above it, which for a nested entry
    means a shifted length prefix and a blank or truncated filename.

    The size is kept under 128 deliberately -- a larger varint puts a 0x80
    continuation byte in the entry, the entry decodes as ('raw', ...), and
    the re-encode path this pins never runs.
    """
    raw = _list_files_response("presentación.pptx", "conv-1/presentación.pptx",
                               100, "application/pdf")
    monkeypatch.setattr(grok_backend, "get_channel", lambda: _FakeChannel(raw))
    monkeypatch.setattr(grok_backend, "_make_meta", lambda: [])
    monkeypatch.setattr(grok_backend, "_stream_raw_chat",
                        lambda req_bytes, timeout=120: _chat_chunks("conv-1", "hi"))
    monkeypatch.setattr(grok_backend, "_resolve_file_url",
                        lambda conv_id, file_path, timeout=15:
                            f"https://cdn.example/{file_path}")

    result = "".join(grok_backend.stream_chat("prompt", "grok-plugins-test"))

    assert "[Descargar: **presentación.pptx**]" in result
    assert "https://cdn.example/presentación.pptx" in result


def test_list_files_preserves_a_non_ascii_filename(monkeypatch):
    """The same invariant one level down, where GET
    /grok/conversations/{id}/files reads it: the parsed entry itself, not
    stream_chat's rendering of it."""
    raw = _list_files_response("informe-año.pdf", "conv-1/informe-año.pdf",
                               100, "application/pdf")
    monkeypatch.setattr(grok_backend, "get_channel", lambda: _FakeChannel(raw))
    monkeypatch.setattr(grok_backend, "_make_meta", lambda: [])

    files = grok_backend.list_files("conv-1")

    assert [f["filename"] for f in files] == ["informe-año.pdf"]
    assert files[0]["storage_path"] == "conv-1/informe-año.pdf"
