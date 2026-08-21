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
    """The eleven booleans, and where each one's confidence comes from.

    Two different standards of evidence back these values, and the difference
    matters more here than anywhere else in this repo: this contract exists
    to stop proxies overclaiming, so it must not open by overclaiming itself.

      MEASURED against a live backend -- a real request was issued and a real
      response was read:
        `chat`, `streaming` (the proxy's daily path), `tools` (measured
        live against the model pool), `vision` (30 of 31 pool routes read a
        4-digit code out of a test image), `images` (the imagine-agent-mode
        family, exercised live).

      DERIVED from the decompiled APK's protocol -- the service name, the
      method name and the field tags are recovered from grok's own client, the
      request frames are built to match, and the parsing is pinned by tests
      that stub `_raw_stream`/`_raw_unary`. What has NOT happened is a call to
      the real RPC:
        `audio_speech` (Chat/TextToSpeech), `audio_transcription`
        (Voice/SpeechToText), `conversations` (Chat/ListConversations,
        Chat/GetConversation) and `search` (the inverted `disable_search`
        field on Chat/AddResponse).

      Neither, because they are FALSE by inspection rather than by
      measurement: `translate` (grok publishes no translate RPC at all) and
      `files` (the upload RPC is real and verified, but the account-wide
      registry it would need is unmeasured -- see that bullet for the one
      live probe that would settle it).

    A derived boolean is a claim the gateway will act on before anyone has
    seen it work. If one of the four is wrong, the failure is a routed request
    that fails, then ordinary failover and suspicion (spec section 5.4) -- and
    the correction goes in `exceptions`, which is the strongest voice.

    The per-capability detail:

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
      - `files` is FALSE, and stays false until measured. Upload itself
        works today, backed by real gRPC (grok_api.Chat/UploadFile) -- but
        it is served at `POST /grok/files`, this proxy's own native surface,
        not at the standard OpenAI path. All four `/v1/files*` endpoints
        (`POST`, `GET`, `GET /{id}`, `DELETE /{id}`) answer 501: the
        contract's `files` boolean promises the whole CRUD surface at the
        standard path, and a capability reported false must mean every
        endpoint under it refuses there, not just the ones that happen to
        lack a working RPC -- so a client that trusts `files: false` is
        never routed to a working call at `/v1/files`, even though that
        call exists one prefix over. Grok's account-wide file registry
        appears to live in a different namespace entirely,
        grok_api_v2.AssetRepository (ListAssetMetadata/GetAssetMetadata/
        DeleteAsset, keyed by asset_id, with fields -- mime_type, name,
        size_bytes, create_time -- that map cleanly onto an OpenAI file
        object), and whether a file uploaded via Chat/UploadFile (keyed by an
        unrelated file_metadata_id) shows up there as an asset has not been
        measured against a live account. The one live probe that would
        settle it: upload a file via /grok/files, then call
        grok_api_v2.AssetRepository/ListAssetMetadata and check whether the
        returned file_id appears as an asset_id. See main.py's /v1/files
        handlers and grok_backend.FileDeleteNotSupported. (grok_api_v2.
        FilesService/ListFiles is real, but it is a conversation-scoped
        virtual filesystem, not this registry -- it is served at
        GET /grok/conversations/{conv_id}/files instead.)
      - `conversations` is TRUE: `GET /v1/conversations` and
        `GET /v1/conversations/{id}` now alias `backend.list_conversations`
        and `backend.get_conversation` at the standard paths §3.4 promises,
        wrapping the list as `{"object": "list", "data": [...]}` and mapping
        `conversation_id` to `id`. The native `/grok/conversations` family is
        untouched and still the richer surface (rename, delete, share,
        messages).
      - `audio_speech` is TRUE: `POST /v1/audio/speech` calls
        `grok_api.Chat/TextToSpeech` (a streaming RPC, concatenated into one
        response) and returns raw audio bytes with the matching Content-Type
        -- not a JSON envelope, since every OpenAI client writes this body
        straight to a file. Voice defaults to the first entry from
        `Voice/ListTopVoices` (grok_backend._default_voice_id) rather than a
        hardcoded id that may not exist on this account.
      - `audio_transcription` is TRUE: `POST /v1/audio/transcriptions` calls
        `grok_api.Voice/SpeechToText` (unary) and returns {"text": ...},
        or plain text when `response_format: "text"`. grok has a second
        speech-to-text RPC, `Voice/Transcribe`, deliberately not used here: it
        returns `TranscribeResponse { 1: segments }`, a repeated structure that
        would have to be stitched into one transcript, where SpeechToText's
        reply already carries a top-level `text` (f1) that maps straight onto
        this boolean's shape. See grok_backend.speech_to_text.
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
        "audio_speech":        live,
        "audio_transcription": live,
        "translate":           False,
        "search":              live,
        "files":               False,
        "conversations":       live,
    }
