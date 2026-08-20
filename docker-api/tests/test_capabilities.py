import capabilities as cap


ACCOUNT = cap.SessionState(mode="account")
ANON = cap.SessionState(mode="anonymous")


def test_every_required_key_is_present_and_boolean():
    for state in (ACCOUNT, ANON):
        e = cap.effective(state)
        assert set(e) == set(cap.REQUIRED_CAPABILITIES)
        assert all(isinstance(v, bool) for v in e.values())


def test_chat_tools_and_vision_are_true_with_a_session():
    e = cap.effective(ACCOUNT)
    assert e["chat"] and e["streaming"] and e["tools"] and e["vision"] and e["images"]


def test_nothing_works_without_a_session():
    # Every grok RPC travels on the session token; without it the proxy has no
    # backend at all, so claiming any capability would be a lie.
    assert not any(cap.effective(ANON).values())


def test_the_capabilities_this_proxy_does_not_serve_yet_are_false():
    e = cap.effective(ACCOUNT)
    assert not e["files"]


def test_translate_is_false_because_grok_has_no_translate_endpoint():
    assert cap.effective(ACCOUNT)["translate"] is False


def test_the_auth_block_reports_no_plan():
    # grok has no tiers. Reporting a plan name here would invent one.
    b = cap.auth_block(ACCOUNT)
    assert b == {"mode": "account", "plan": None,
                 "subscription_active": False, "expires_at": None}


def test_snapshot_follows_the_session_token(monkeypatch):
    monkeypatch.setenv("GROK_SESSION_TOKEN", "t")
    assert cap.snapshot().mode == "account"
    monkeypatch.delenv("GROK_SESSION_TOKEN")
    assert cap.snapshot().mode == "anonymous"
