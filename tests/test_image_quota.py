"""An exhausted image bucket must say WHEN it comes back.

The image buckets are ACCOUNT-level and daily (GetImagineQuotaInfo takes no model
argument), while the same `imagine-agent-mode*` models carry 999 CHAT requests per
hour. Measured 2026-08-19: 987/999 chat requests still unused with every image
bucket at zero.

The consumer in front punishes a route on a 429, and how long for depends entirely
on whether a `Retry-After` arrived: without one it guesses a short default, with
one it honours the provider up to an hour. A daily bucket that resets tomorrow is
exactly the case where guessing is wrong in both directions -- too short and it
retries an empty bucket all day, too long and it parks a healthy route.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docker-api"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grok_backend as backend          # noqa: E402
import main as srv                      # noqa: E402

BODY = {"prompt": "un gato astronauta"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(srv, "verify_key", lambda *a, **k: True)
    return TestClient(srv.app, raise_server_exceptions=False)


def _raise(exc):
    def _boom(*a, **k):
        raise exc
    return _boom


def test_an_exhausted_bucket_answers_429_with_the_reset_as_retry_after(client, monkeypatch):
    monkeypatch.setattr(srv.time, "time", lambda: 1000.0)
    monkeypatch.setattr(backend, "generate_image", _raise(
        backend.ImageQuotaExhausted("no quota", next_available_epoch=1000.0 + 7200)))
    r = client.post("/v1/images/generations", json=BODY)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) == pytest.approx(7200, abs=2)


def test_a_reset_already_past_does_not_produce_a_negative_retry_after(client, monkeypatch):
    monkeypatch.setattr(srv.time, "time", lambda: 9999.0)
    monkeypatch.setattr(backend, "generate_image", _raise(
        backend.ImageQuotaExhausted("no quota", next_available_epoch=1000.0)))
    r = client.post("/v1/images/generations", json=BODY)
    assert int(r.headers["Retry-After"]) >= 0


def test_without_a_known_reset_no_header_is_invented(client, monkeypatch):
    """Better no header than a made-up one: the consumer honours it literally."""
    monkeypatch.setattr(backend, "generate_image", _raise(
        backend.ImageQuotaExhausted("no quota", next_available_epoch=None)))
    r = client.post("/v1/images/generations", json=BODY)
    assert r.status_code == 429
    assert "Retry-After" not in r.headers


def test_it_is_still_a_RuntimeError_so_existing_handling_is_unchanged():
    assert issubclass(backend.ImageQuotaExhausted, RuntimeError)


def test_an_ordinary_failure_is_still_a_502(client, monkeypatch):
    monkeypatch.setattr(backend, "generate_image", _raise(ValueError("boom")))
    assert client.post("/v1/images/generations", json=BODY).status_code == 502
