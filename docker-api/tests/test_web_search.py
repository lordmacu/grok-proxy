import capabilities as cap
import main


def _resolve(**body):
    """What the handlers compute for `disable_search` from a request body."""
    return main.resolve_disable_search(main.ChatRequest(**body))


def _resolve_add_response(**body):
    """Same resolver, exercised through the other model that carries it:
    AddResponseRequest, used by /grok/conversations/{id}/messages. Pinned
    separately so the two models cannot silently desync -- resolve_disable_search
    duck-types across both, and nothing else caught that before this test.
    """
    body.setdefault("message", "hi")
    return main.resolve_disable_search(main.AddResponseRequest(**body))


def test_search_is_on_by_default():
    # The behaviour change: grok used to answer ungrounded unless asked.
    assert _resolve(messages=[{"role": "user", "content": "hi"}]) is False


def test_web_search_false_turns_it_off():
    assert _resolve(messages=[{"role": "user", "content": "hi"}],
                    web_search=False) is True


def test_web_search_true_turns_it_on():
    assert _resolve(messages=[{"role": "user", "content": "hi"}],
                    web_search=True) is False


def test_the_native_disable_search_still_works():
    assert _resolve(messages=[{"role": "user", "content": "hi"}],
                    disable_search=True) is True


def test_web_search_wins_over_the_native_field():
    # Two ways to say the same thing; the standard one is authoritative.
    assert _resolve(messages=[{"role": "user", "content": "hi"}],
                    web_search=True, disable_search=True) is False


def test_the_contract_now_claims_search():
    assert cap.effective(cap.SessionState(mode="account"))["search"] is True
    assert cap.effective(cap.SessionState(mode="anonymous"))["search"] is False


# AddResponseRequest (/grok/conversations/{id}/messages) carries the same two
# fields as ChatRequest and runs through the same resolver, but nothing built
# on ChatRequest exercises it -- pinned here so a future rename that desyncs
# the two models fails loudly instead of shipping green.
def test_add_response_search_is_on_by_default():
    assert _resolve_add_response() is False


def test_add_response_web_search_wins_over_the_native_field():
    assert _resolve_add_response(web_search=True, disable_search=True) is False
