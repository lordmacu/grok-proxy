from fastapi.testclient import TestClient

import capabilities as cap
import main

REQUIRED = set(cap.REQUIRED_CAPABILITIES)


def _health(monkeypatch, state):
    monkeypatch.setattr(cap, "snapshot", lambda: state)
    with TestClient(main.app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    return r.json()


def test_health_declares_the_contract(monkeypatch):
    body = _health(monkeypatch, cap.SessionState(mode="account"))
    assert body["contract"] == 1
    assert body["provider"] == "grok"


def test_capabilities_are_exactly_the_required_booleans(monkeypatch):
    body = _health(monkeypatch, cap.SessionState(mode="account"))
    assert set(body["capabilities"]) == REQUIRED
    assert all(isinstance(v, bool) for v in body["capabilities"].values())


def test_the_auth_block_names_no_plan(monkeypatch):
    body = _health(monkeypatch, cap.SessionState(mode="account"))
    assert body["auth"]["mode"] == "account"
    assert body["auth"]["plan"] is None


def test_health_needs_no_api_key(monkeypatch):
    # The gateway sweeps this on a schedule and it is the container health
    # check; requiring a key would make both depend on configuration they do
    # not carry.
    monkeypatch.setattr(main, "API_KEY", "a-secret")
    body = _health(monkeypatch, cap.SessionState(mode="account"))
    assert body["contract"] == 1


def test_the_legacy_fields_survive(monkeypatch):
    body = _health(monkeypatch, cap.SessionState(mode="account"))
    assert body["status"] == "ok"
    assert "version" in body
    assert "session_configured" in body
    assert "high_rate_pool_size" in body
