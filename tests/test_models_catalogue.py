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
from main import list_models     # noqa: E402


def _by_id():
    return {m["id"]: m for m in list_models()["data"]}


def test_an_abundant_agent_publishes_its_real_hourly_capacity():
    assert _by_id()["grok-plugins-4p6-excel"]["requests_per_hour"] == 999


def test_a_scarce_model_publishes_a_rate_per_HOUR_not_per_window():
    """grok-3 is 30 requests per 24h. Published as 30 it reads as abundant."""
    entry = _by_id()["grok-3"]
    assert entry["window_hours"] == 24
    assert entry["requests_per_hour"] == 30 / 24


def test_every_direct_model_publishes_the_field():
    entries = _by_id()
    for mid in backend.MODELS_CATALOG:
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


def test_grok_3_is_a_direct_model_not_an_alias():
    """It maps to itself in MODEL_ALIASES, so it is a real route, not a pool entry."""
    assert backend.MODEL_ALIASES["grok-3"] == "grok-3"
    assert not _by_id()["grok-3"].get("description", "").startswith("Alias")
