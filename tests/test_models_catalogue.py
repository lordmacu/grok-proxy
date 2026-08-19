"""What /v1/models tells a consumer about capacity and about aliases.

Both facts exist in this backend already and neither reached the catalogue in a
form a consumer could act on, with measurable consequences for the gateway in
front (llm-libre, 2026-08-19):

- The 33 entries looked interchangeable, while real sustained capacity spans
  three orders of magnitude -- `grok-plugins-*` and `imagine-agent-mode*` carry
  999/hour EACH on independent windows, `grok-3` carries 30 per 24h. The gateway
  probed them alike and its quality battery (5 requests per route per run) ate 19
  of grok-3's 30 daily requests.
- Nine of the entries are ALIASES round-robining over that same abundant pool.
  The gateway measured each as its own route -- nine independent quality scores
  for one pool -- and published `claude-3-sonnet: quality 1.0` in its ranking,
  which is Grok 4.5 wearing a borrowed name.

`rate_per_hour` was already published but is a rate per WINDOW, not per hour:
grok-3 reports 30 with `window_hours: 24`. `requests_per_hour` is the same fact
with no arithmetic left for the reader to get wrong.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docker-api"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grok_backend as backend   # noqa: E402
from main import ADVERTISE_MIN_RATE_PER_HOUR, list_models   # noqa: E402


def _by_id():
    return {m["id"]: m for m in list_models()["data"]}


def test_an_abundant_agent_publishes_its_real_hourly_capacity():
    assert _by_id()["grok-plugins-4p6-excel"]["requests_per_hour"] == 999


def test_the_published_rate_is_per_HOUR_not_per_window():
    """`rate_per_hour` is a rate per WINDOW despite its name -- grok-3 reports 30
    alongside window_hours 24, which reads as abundant and is really 1.25/h.
    `requests_per_hour` is the same fact with the arithmetic already done.

    (grok-3 itself is no longer advertised; that this exact arithmetic is what
    excludes it is pinned by test_the_scarce_models_are_not_advertised.)"""
    for mid, entry in _by_id().items():
        if "requests_per_hour" not in entry:
            continue
        info = backend.MODELS_CATALOG[mid]
        assert entry["requests_per_hour"] == info["rate"] / info["window_h"], mid


def test_every_advertised_direct_model_publishes_the_field():
    entries = _by_id()
    for mid in backend.MODELS_CATALOG:
        if mid in entries:
            assert "requests_per_hour" in entries[mid], mid


def test_the_pool_aliases_declare_themselves_as_aliases():
    """A consumer that de-duplicates aliases (llm-libre does, by this exact
    prefix) must be able to see them for what they are."""
    entries = _by_id()
    for alias, target in backend.MODEL_ALIASES.items():
        if target is None:
            assert entries[alias]["description"].startswith("Alias"), alias


def test_a_real_model_is_not_marked_as_an_alias():
    assert not _by_id()["grok-plugins-4p6-excel"].get("description", "").startswith("Alias")


def test_grok_3_is_a_real_model_and_is_withheld_for_being_scarce():
    """It maps to ITSELF in MODEL_ALIASES rather than to the pool, so it is a real
    model, not a pool entry -- which is exactly why it has to be filtered out of
    BOTH loops. Filtering only the direct-model one let it back in through the
    alias loop with no rate fields attached, looking abundant."""
    assert backend.MODEL_ALIASES["grok-3"] == "grok-3"
    assert backend.MODELS_CATALOG["grok-3"]["rate"] / \
           backend.MODELS_CATALOG["grok-3"]["window_h"] < ADVERTISE_MIN_RATE_PER_HOUR
    assert "grok-3" not in _by_id()


# --- Only sustainable models are advertised, 2026-08-19 ----------------------
#
# Six of the entries carry quotas the gateway in front cannot route on: grok-4 at
# 7 per 24h, grok-3 at 30, grok-420 and grok-4-1-thinking at 8 per 4h, the two
# companions at 10/h. Underneath, grok-3 is the same Grok 4.5 the 999/h pool
# already serves without a meter.
#
# Advertising them is not free even when nothing routes to them: the gateway's
# quality battery costs five requests per route per run, which is 17% of grok-3's
# ENTIRE daily budget for one run. On the day this was measured, that probing had
# consumed 19 of its 30 daily requests -- spent proving a scarce model works,
# while the abundant pool of the same model sat idle.
#
# They stay CALLABLE by explicit id and stay listed in /v1/models/rates, which is
# the diagnostics view. They are simply no longer offered for routing.


def test_the_scarce_models_are_not_advertised():
    advertised = set(_by_id())
    for mid, info in backend.MODELS_CATALOG.items():
        if info["rate"] / info["window_h"] < ADVERTISE_MIN_RATE_PER_HOUR:
            assert mid not in advertised, mid


def test_every_advertised_model_can_sustain_the_floor():
    for m in _by_id().values():
        rate = m.get("requests_per_hour")
        if rate is not None:
            assert rate >= ADVERTISE_MIN_RATE_PER_HOUR, m["id"]


def test_the_fourteen_plugin_agents_survive():
    """999/h AND real custom tool calling: these are what carries the traffic."""
    advertised = set(_by_id())
    plugins = [m for m in backend.MODELS_CATALOG if m.startswith("grok-plugins-")]
    assert len(plugins) == 14
    assert set(plugins) <= advertised


def test_the_image_agents_survive_even_though_they_have_no_tools():
    """They are three of the FOUR routes in the consumer's whole catalogue that
    can generate an image. Dropping them for lacking tools would leave image
    generation on a single route with no failover -- the exact fragility this
    work exists to remove."""
    advertised = set(_by_id())
    imagine = [m for m in backend.MODELS_CATALOG if m.startswith("imagine-agent-mode")]
    assert len(imagine) == 3
    assert set(imagine) <= advertised


def test_the_rates_endpoint_still_reports_everything():
    """Dropping a model from the offer must not drop it from diagnostics: an
    operator asking "why can I not reach grok-3" needs to see its quota."""
    from main import models_rates
    reported = {m["model"] for m in models_rates()["models"]}
    assert set(backend.MODELS_CATALOG) <= reported


def test_a_scarce_model_is_still_callable_by_explicit_id():
    """Not advertised is not removed: the backend still knows it."""
    assert "grok-3" in backend.MODELS_CATALOG
