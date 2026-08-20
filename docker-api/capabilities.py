"""What this proxy can actually do right now.

Spec: the proxy capability contract, llm-libre
docs/superpowers/specs/2026-08-20-proxy-capability-contract-design.md

THE RULE: a boolean says what a request sent right now would ACHIEVE, not what
this codebase implements. Where the two differ, the endpoint is the liar and
this module is the correction.

Where the rule STOPS: a boolean tracks entitlement, not the meter. grok's image
quota running out is a 429 the gateway already handles with a cooldown and
recovers from on its own; it must never flip `images`. The dividing line is
durability -- if a fresh request tomorrow would still be refused for the same
reason, it belongs in the boolean.

Unlike chatgpt-proxy, there is no plan to resolve: grok has no tiers, and every
RPC travels on one session token. So the whole entitlement story is whether that
token is configured, `snapshot()` is a single environment read, and `auth_block`
reports `plan: null` rather than inventing a tier name.
"""
import os
from dataclasses import dataclass

# The eleven keys the contract requires, byte-for-byte the set the gateway
# validates against (llm_libre.contract.REQUIRED_CAPABILITIES). Duplicated
# rather than imported because the two live in different repos and deploy
# independently; tests/test_health_contract.py is what keeps them honest.
REQUIRED_CAPABILITIES = (
    "chat", "streaming", "tools", "vision", "images",
    "audio_speech", "audio_transcription", "translate",
    "search", "files", "conversations",
)


@dataclass(frozen=True)
class SessionState:
    mode: str          # "account" | "anonymous"


def snapshot() -> SessionState:
    """Whether this process has a session token. That is all grok's auth is."""
    token = (os.environ.get("GROK_SESSION_TOKEN") or "").strip()
    return SessionState(mode="account" if token else "anonymous")


def auth_block(state: SessionState) -> dict:
    """The contract's informational `auth` block.

    Every field except `mode` is null on purpose: grok sells no tiers, so there
    is no plan to name and no subscription to expire. Reporting a placeholder
    here would be the same class of lie the contract exists to end.
    """
    return {"mode": state.mode, "plan": None,
            "subscription_active": False, "expires_at": None}


def effective(state: SessionState) -> dict:
    """The eleven booleans. Every value below was measured, not assumed.

      - `tools` is TRUE and this is the unusual one: grok returns real
        tool_calls natively, no prompt-based emulation needed. What the
        gateway actually measured is subtler than a pass rate: grok is a POOL
        with aliases rather than 31 distinct models, so an id not in the
        static catalog round-robins across backends on every call
        (grok_backend.resolve_model). Measuring a single id once can
        contradict measuring it again for that reason alone -- what looks like
        a flaky model is really the roulette. The gateway declares
        `tools: true` for the pool as a whole, with the imagine-agent-mode
        family excepted: 0/3 on tool_calls when repeated, because those are
        image-generation agents, not chat models.
      - `vision` is TRUE, served inside /v1/chat/completions: image_url content
        parts are uploaded and the request is steered to a vision-capable
        model. The gateway measured 30 of 31 routes correctly reading a
        4-digit code out of a test image.
      - `images` is TRUE via the imagine-agent-mode family, the only grok models
        that generate.
      - `search` is TRUE, served through `web_search` on /v1/chat/completions
        (and the conversation-message endpoints): grok's native gRPC field is
        `disable_search`, inverted and defaulting to search ON, matching what
        every other provider does. See main.resolve_disable_search.
      - `files` is TRUE, served through the OpenAI-shaped `/v1/files` surface
        (POST, GET, GET-by-id, DELETE): create, list and fetch-by-id route to
        real gRPC (grok_api.Chat/UploadFile, grok_api_v2.FilesService/ListFiles).
        Delete is the exception: the only delete-shaped RPC found in the
        decompiled APK, grok_api_v2.AssetRepository/DeleteAsset, belongs to a
        distinct asset-versioning resource with no established link to a chat
        file id, so `DELETE /v1/files/{id}` answers 501 rather than guessing
        against a destructive call. See grok_backend.FileDeleteNotSupported.
      - `conversations`, `audio_speech` and `audio_transcription` are FALSE
        *for now*: the backend can do all three, but not yet at the paths §3.4
        of the contract promises. Each flips in the same commit that makes its
        endpoint real.
      - `translate` is FALSE and stays false: grok has no translate endpoint,
        and routing it through a chat turn would be a different capability
        wearing this one's name.
    """
    live = state.mode == "account"
    return {
        "chat":                live,
        "streaming":           live,
        "tools":               live,
        "vision":              live,
        "images":              live,
        "audio_speech":        False,
        "audio_transcription": False,
        "translate":           False,
        "search":              live,
        "files":               live,
        "conversations":       False,
    }
