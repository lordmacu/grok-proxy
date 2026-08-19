"""Grok's stream interleaves status headers with the answer; only the answer is content.

Captured from the live proxy on 2026-08-18. Each message of the
CreateConversationAndRespond stream carries a kind in field 18:

  final               the answer itself, token by token (910 of 925 here)
  header              a status label shown while the answer is being written
  tool_usage_card     the <xai:...> card for a tool call

The bug this pins: every field-2 string was fed to the cleaner regardless of its
kind, so the headers landed INSIDE `content` -- at whatever token boundary they
happened to arrive at. Measured against the gateway the same day, one answer came
back as `..."anio":1994Generando recomendaciones de peliculas,"tipo":"movie"...`,
which is a status label spliced into the middle of a JSON value.

The headers are generated per query and localized ("Compilando las 20
recomendaciones", "Crafting the final JSON response"), so no phrase list can
catch them -- which is why `_THINKING`, matching the single English phrase
"Thinking about your request", let every other one through. Field 18 is the
discriminator that does not depend on wording.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker-api"))

import grok_backend as gb

FIXTURE = Path(__file__).parent / "fixtures" / "create_conversation_stream.json"


def _assemble(messages) -> str:
    """Exactly what stream_chat does with the decoded messages of the stream."""
    cleaner = gb._StreamCleaner()
    out = []
    for msg in messages:
        if gb.is_status_header(msg["f18"]):
            continue
        token = msg["f2"]
        if token:
            piece = cleaner.feed(token)
            if piece:
                out.append(piece)
    out.append(cleaner.flush())
    return "".join(out)


@pytest.fixture(scope="module")
def stream():
    return json.loads(FIXTURE.read_text())["messages"]


def test_the_captured_stream_really_does_carry_status_headers(stream):
    """Guards the fixture itself: without headers in it the test below proves nothing."""
    headers = [m["f2"] for m in stream if m["f18"] == "header"]
    assert headers, "fixture no longer contains status headers"
    assert "Compilando las recomendaciones" in headers


def test_no_status_header_reaches_the_content(stream):
    content = _assemble(stream)
    for header in {m["f2"] for m in stream if m["f18"] == "header"}:
        assert header not in content, f"status header leaked into content: {header!r}"


def test_the_answer_survives_intact(stream):
    """Dropping the headers must not cost a single character of the answer.

    The captured answer is a JSON array, so parsing it is a stricter check than
    comparing lengths: a header spliced mid-value breaks the parse, and any
    token dropped along with the headers would too.
    """
    content = _assemble(stream)
    raw = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    assert start >= 0 and end > start, f"no JSON array in content: {content[:200]!r}"
    items = json.loads(raw[start:end + 1])
    assert len(items) == 20
    assert all(item["titulo"] for item in items)


def test_content_starts_at_the_answer(stream):
    """The headers arrive BEFORE the first answer token, so a leak shows up as a
    prefix -- which is how this was first seen from the client side."""
    assert _assemble(stream).lstrip().startswith("[")


@pytest.mark.parametrize("kind", ["final", "response_start", "", None])
def test_answer_bearing_kinds_are_never_dropped(kind):
    assert not gb.is_status_header(kind)


def test_only_the_header_kind_is_dropped():
    assert gb.is_status_header("header")
