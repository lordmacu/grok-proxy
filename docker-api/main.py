"""
API OpenAI-compatible sobre Grok gRPC.

Endpoints:
  GET  /health
  GET  /v1/models
  GET  /v1/models/{model_id}
  POST /v1/chat/completions     streaming y no-streaming, tools básico
  POST /auth/login              email + contraseña
  POST /auth/otp/send           envía OTP (cuentas Twitter/Google)
  POST /auth/otp/verify         verifica OTP → devuelve session_token

Parámetros OpenAI soportados:
  model            → mapeado al pool de alta tasa o al modelo específico
  messages         → convertido a prompt único con roles
  stream           → SSE token a token
  tools            → inyectados como contexto de sistema; respuesta parseada
  tool_choice      → ignorado (se detectan tool calls en la respuesta)
  is_reasoning     → pasa is_reasoning=True a Grok (modelos con razonamiento)
  web_search       → standard field (llm-libre spec §3.4); search ON by default
  disable_search   → grok's native field; still works, but `web_search` wins
                      when both are present (see resolve_disable_search)

Parámetros ignorados (Grok no los soporta en gRPC):
  temperature, top_p, max_tokens, stop, frequency_penalty, presence_penalty,
  n, logprobs, logit_bias, seed, response_format
"""
from __future__ import annotations
import os, time, uuid, json, base64
from typing import AsyncIterator, Optional, Union, Any

from fastapi import FastAPI, HTTPException, Depends, Header, Path, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

import grok_backend as backend
import auth_backend as auth
import capabilities

API_KEY     = os.environ.get("API_KEY", "")
APP_VERSION = "1.0.0"

app = FastAPI(
    title="Grok OpenAI Proxy",
    version=APP_VERSION,
    description=__doc__,
    docs_url="/docs",
)


# ── Auth middleware ───────────────────────────────────────────────────────────
def verify_key(authorization: str = Header(default="")):
    if not API_KEY:
        return
    token = authorization.removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Schemas ───────────────────────────────────────────────────────────────────
class Message(BaseModel):
    role:     str
    content:  Union[str, list, None] = None
    name:     Optional[str]          = None
    tool_calls: Optional[list]       = None
    tool_call_id: Optional[str]      = None


class ChatRequest(BaseModel):
    model:              str              = "grok-4.5"
    messages:           list[Message]
    stream:             bool             = False
    tools:              Optional[list]   = None
    tool_choice:        Optional[Any]    = None
    # Parámetros OpenAI aceptados pero sin efecto en Grok gRPC:
    temperature:        Optional[float]  = None
    top_p:              Optional[float]  = None
    max_tokens:         Optional[int]    = None
    stop:               Optional[Any]    = None
    frequency_penalty:  Optional[float]  = None
    presence_penalty:   Optional[float]  = None
    n:                  Optional[int]    = None
    seed:               Optional[int]    = None
    # Extensiones Grok nativas
    is_reasoning:       bool             = False
    disable_search:     Optional[bool]   = None
    # The contract's standard name (llm-libre spec §3.4), and the default flips
    # with it: search ON unless a caller says otherwise, which is what every
    # other provider does and what a caller reasonably expects. `disable_search`
    # is grok's own inverted field and keeps working for whatever already sends
    # it; `web_search` wins when both arrive, because it is the standard one.
    web_search:         Optional[bool]   = None
    temporary:          bool             = False   # f3: efímera, no queda en historial
    force_artifact:     bool             = False   # f40: fuerza bloque de código
    enabled_skills:     Optional[list[int]] = None # f74: IDs de skill [1=docx,3=pdf,4=pptx,7=xlsx]


def resolve_disable_search(req) -> bool:
    """The native `disable_search` value for a request, from either field.

    Returns a bool because that is what the backend takes. The precedence is
    `web_search` (standard, inverted) over `disable_search` (native) over the
    default, and the default is now "search on" -- see the field comment.
    """
    if req.web_search is not None:
        return not req.web_search
    if req.disable_search is not None:
        return req.disable_search
    return False


class LoginRequest(BaseModel):
    email:    str
    password: str

class OtpSendRequest(BaseModel):
    email: str

class OtpVerifyRequest(BaseModel):
    email: str
    code:  str


# ── Helpers de respuesta OpenAI ───────────────────────────────────────────────
def _cid() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _chunk(cid: str, model: str, content: str = "", finish: Optional[str] = None,
           tool_calls: Optional[list] = None) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    if not delta and not finish:
        delta["role"] = "assistant"
    return "data: " + json.dumps({
        "id":      cid,
        "object":  "chat.completion.chunk",
        "created": int(time.time()),
        "model":   model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n"


def _full(cid: str, model: str, content: str,
          tool_calls: Optional[list] = None) -> dict:
    message: dict = {"role": "assistant", "content": content or None}
    finish = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        message["content"]    = None
        finish = "tool_calls"
    return {
        "id":      cid,
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage":   {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── /health ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    state = capabilities.snapshot()
    return {
        "status":  "ok",
        "version": APP_VERSION,
        # The capability contract (llm-libre spec 2026-08-20). `capabilities`
        # is EFFECTIVE: what a request would achieve right now, so the gateway
        # reads one boolean instead of learning what a grok session is.
        "contract": 1,
        "provider": "grok",
        "auth": capabilities.auth_block(state),
        "capabilities": capabilities.effective(state),
        # Kept: existing dashboards and the container health check read these.
        "session_configured":  state.mode == "account",
        "high_rate_pool_size": len(backend.HIGH_RATE_POOL),
    }


# Sustained requests/hour a model must be able to carry to be OFFERED in
# /v1/models. Below it a model is still callable by explicit id and still fully
# reported by /v1/models/rates -- it is simply not advertised for routing.
#
# Six models sit below this line: grok-4 (7 per 24h), grok-3 (30 per 24h),
# grok-420 and grok-4-1-thinking-1129 (8 per 4h), and the two companions (10/h).
# Underneath, grok-3 is the same Grok 4.5 the 999/h pool already serves unmetered.
#
# Advertising them was not free even when nothing routed to them. The gateway in
# front runs a quality battery of five requests PER ROUTE per run -- 17% of
# grok-3's entire daily budget for a single run. Measured 2026-08-19: that probing
# had consumed 19 of its 30 daily requests, spent proving a scarce model works
# while the abundant pool of the same model sat idle.
ADVERTISE_MIN_RATE_PER_HOUR = float(os.getenv("ADVERTISE_MIN_RATE_PER_HOUR", "60"))

# The only grok family that generates images. Reused, not redefined:
# `backend.IMAGE_POOL` is already exactly these three ids (every model whose
# name starts with "imagine-agent-mode"); a second definition here would drift
# from it the first time either list changes.
_IMAGINE_MODELS = frozenset(backend.IMAGE_POOL)


# ── /v1/models ────────────────────────────────────────────────────────────────
@app.get("/v1/models", dependencies=[Depends(verify_key)])


def list_models():
    ts     = int(time.time())
    models = []

    # Aliases OpenAI-compatibles → round-robin sobre el pool completo.
    #
    # They declare themselves as aliases in `description`. A consumer that
    # de-duplicates (llm-libre does, on exactly this prefix) would otherwise treat
    # nine names for ONE pool as nine independent routes: nine quality scores
    # measuring the same thing, nine shares of the probe budget, and a public
    # ranking claiming `claude-3-sonnet: quality 1.0` -- which is Grok 4.5 wearing
    # a borrowed name, not Claude.
    for alias, target in backend.MODEL_ALIASES.items():
        pooled = target is None
        # An alias POINTING AT a scarce model inherits its scarcity -- `grok-3`
        # is such an alias of itself. Filtering only the direct-model loop below
        # let it back in through this one, with no rate fields attached, which is
        # worse than not filtering at all: it looked abundant.
        if not pooled:
            info = backend.MODELS_CATALOG.get(target)
            if info and info["rate"] / info["window_h"] < ADVERTISE_MIN_RATE_PER_HOUR:
                continue
        entry = {
            "id":       alias,
            "object":   "model",
            "created":  ts,
            "owned_by": "grok",
            "notes":    "round-robin over the 999/h pool" if pooled else "",
        }
        if pooled:
            entry["description"] = (
                "Alias → round-robin over the %d models in the 999/h pool"
                % len(backend.HIGH_RATE_POOL))
        models.append(entry)

    # Modelos internos directos (se puede pedir uno específico).
    #
    # Only those that can sustain ADVERTISE_MIN_RATE_PER_HOUR are OFFERED. The
    # `imagine-agent-mode*` agents are kept even though they declare no tools:
    # they are three of the four routes in the consumer's entire catalogue that
    # can generate an image, and dropping them would leave image generation on a
    # single route with no failover.
    for mid, info in backend.MODELS_CATALOG.items():
        if info["rate"] / info["window_h"] < ADVERTISE_MIN_RATE_PER_HOUR:
            continue
        models.append({
            "id":            mid,
            "object":        "model",
            "created":       ts,
            "owned_by":      "grok",
            "rate_per_hour": info["rate"],
            "window_hours":  info["window_h"],
            # The same fact as the two fields above with the arithmetic already
            # done, because `rate_per_hour` is a rate per WINDOW despite its name:
            # grok-3 reports 30 with window_hours 24, which reads as abundant and
            # is really 1.25/h. A consumer deciding how hard it may lean on a route
            # should not have to notice that.
            "requests_per_hour": info["rate"] / info["window_h"],
            "notes":         info["notes"],
        })

    # --- capability contract: per-model metadata -----------------------------
    # The provider-level block cannot say that the imagine family draws and
    # neither chats nor sees. A per-model value may only NARROW the
    # provider-level one -- `and provider_level[...]` is that rule applied at
    # the source, so an anonymous process reports False for everything.
    #
    # `context_window` is deliberately ABSENT: grok publishes no context figure
    # for any model, and inventing one here is exactly the mistake this contract
    # exists to end.
    provider_level = capabilities.effective(capabilities.snapshot())
    for entry in models:
        draws = entry["id"] in _IMAGINE_MODELS
        entry["max_output_tokens"] = 0 if draws else 8192
        entry["capabilities"] = {
            "tools":  (not draws) and provider_level["tools"],
            "vision": (not draws) and provider_level["vision"],
            "images": draws and provider_level["images"],
        }

    return {"object": "list", "data": models}


# ── /v1/models/rates — rate limits en tiempo real (ANTES del wildcard) ───────
@app.get("/v1/models/rates", dependencies=[Depends(verify_key)])
def models_rates():
    """Rate limits actuales consultados en tiempo real a Grok."""
    live = backend.get_rate_limits_live()
    result = []
    for mid, info in backend.MODELS_CATALOG.items():
        live_data = live.get(mid, {})
        result.append({
            "model":          mid,
            "notes":          info["notes"],
            "rate_per_hour":  info["rate"],
            "window_hours":   info["window_h"],
            "remaining":      live_data.get("remaining"),
            "total":          live_data.get("total"),
            "window_seconds": live_data.get("window_seconds"),
        })
    return {"models": result, "pool_current": backend._rotator.current()}


@app.get("/v1/models/{model_id:path}", dependencies=[Depends(verify_key)])
def get_model(model_id: str):
    ts = int(time.time())
    info = backend.MODELS_CATALOG.get(model_id)
    if not info and model_id not in backend.MODEL_ALIASES:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    base = {"id": model_id, "object": "model", "created": ts, "owned_by": "grok"}
    if info:
        base.update({
            "rate_per_hour": info["rate"],
            "window_hours":  info["window_h"],
            "notes":         info["notes"],
        })
    return base


def _upload_images_from_messages(messages: list[dict]) -> list[str]:
    """
    Extrae imágenes base64 de los mensajes y las sube a Grok.
    Retorna lista de file_ids listos para pasar a stream_chat/complete_chat.
    Ignora silenciosamente las imágenes que fallen al subirse.
    """
    image_data = backend.extract_images_from_messages(messages)
    if not image_data:
        return []
    file_ids = []
    for i, (img_bytes, mime) in enumerate(image_data):
        ext = mime.split("/")[-1].replace("jpeg", "jpg")
        filename = f"image_{i}.{ext}"
        try:
            result = backend.upload_file(filename, content=img_bytes, mime_type=mime)
            fid = result.get("file_id")
            if fid:
                file_ids.append(fid)
        except Exception:
            pass
    return file_ids


# ── /v1/chat/completions ──────────────────────────────────────────────────────
@app.post("/v1/chat/completions", dependencies=[Depends(verify_key)])
async def chat_completions(req: ChatRequest):
    msgs_raw = [m.model_dump() for m in req.messages]
    prompt, system  = backend.messages_to_prompt(msgs_raw, tools=req.tools)

    # Subir imágenes adjuntas (content tipo lista con image_url)
    image_file_ids = _upload_images_from_messages(msgs_raw)

    # Si hay imágenes, usar un modelo con visión confirmada (grok-plugins 999/h)
    if image_file_ids:
        model_id = backend.resolve_vision_model(req.model)
    else:
        model_id = backend.resolve_model(req.model)

    cid = _cid()

    if req.stream:
        async def sse() -> AsyncIterator[str]:
            yield _chunk(cid, req.model)   # primer chunk con role
            full_text = []
            try:
                for token in backend.stream_chat(
                    prompt, model_id,
                    is_reasoning=req.is_reasoning,
                    disable_search=resolve_disable_search(req),
                    system=system,
                    image_file_ids=image_file_ids or None,
                    temporary=req.temporary,
                    force_artifact=req.force_artifact,
                    enabled_skills=req.enabled_skills,
                ):
                    full_text.append(token)
                    # Si hay tools, no emitir hasta el final (necesitamos parsear)
                    if not req.tools:
                        yield _chunk(cid, req.model, token)
            except Exception as e:
                yield _chunk(cid, req.model, f"\n[Error: {e}]", finish="stop")
                yield "data: [DONE]\n\n"
                return

            if req.tools:
                text, calls = backend.parse_tool_calls("".join(full_text))
                if calls:
                    yield _chunk(cid, req.model, tool_calls=calls, finish="tool_calls")
                else:
                    yield _chunk(cid, req.model, text)
            yield _chunk(cid, req.model, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        try:
            content = backend.complete_chat(
                prompt, model_id,
                is_reasoning=req.is_reasoning,
                disable_search=resolve_disable_search(req),
                system=system,
                image_file_ids=image_file_ids or None,
                temporary=req.temporary,
                force_artifact=req.force_artifact,
                enabled_skills=req.enabled_skills,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

        tool_calls = None
        if req.tools:
            content, tool_calls = backend.parse_tool_calls(content)
            if not tool_calls:
                tool_calls = None

        return JSONResponse(_full(cid, req.model, content, tool_calls))


# ── /v1/images/quota ─────────────────────────────────────────────────────────
@app.get("/v1/images/quota", dependencies=[Depends(verify_key)])
def image_quota():
    """
    Consulta el estado del cupo de generación de imágenes (y video).
    Llama grok_api.Media/GetImagineQuotaInfo.

    Buckets:
      image      → imágenes en modo fast  (quality_mode="fast")
      image_pro  → imágenes en modo quality (quality_mode="quality")
      image_edit → edición de imágenes
      video      → generación de video
      video_720p → video 720p

    Cada bucket:
      available          (bool)
      remaining          (int|null)   — queries restantes en la ventana actual
      window_secs        (int)        — tamaño de la ventana (86400 = 24h)
      next_available_at  (str|null)   — ISO 8601 UTC del próximo reset
    """
    try:
        return backend.get_imagine_quota()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /v1/images/generations ───────────────────────────────────────────────────
class ImageRequest(BaseModel):
    prompt:       str
    n:            int            = Field(default=1, ge=1, le=4)
    size:         str            = "1024x1024"   # ignorado (Grok decide el tamaño)
    model:        Optional[str]  = None          # None = rota entre los 3 imagine models
    quality:      str            = "standard"    # "standard"=fast, "hd"=quality
    style:        str            = "vivid"       # ignorado
    quality_mode: Optional[str]  = None          # override explícito: "fast" | "quality"


@app.post("/v1/images/generations", dependencies=[Depends(verify_key)])
def image_generations(req: ImageRequest):
    """
    Genera imágenes con Grok Imagine (Aurora).

    Estrategia automática (si no se pasa quality_mode):
    - Consulta cuota disponible: si "image" (fast) tiene remaining, usa fast.
    - Si "image" agotado, intenta "image_pro" (quality).
    - Fallback a imagine-agent-mode variants (V1).

    quality_mode override: "fast" o "quality" — fuerza un modo específico.
    quality="hd" es equivalente a quality_mode="quality".

    model puede ser: imagine-agent-mode | imagine-agent-mode-dev | imagine-agent-mode-grok-4-5
    """
    prompt = req.prompt
    if req.n > 1:
        prompt = f"Generate {req.n} variations of: {req.prompt}"

    # Resolver quality_mode: override explícito > header quality > automático
    qmode = req.quality_mode
    if qmode is None and req.quality == "hd":
        qmode = "quality"

    try:
        images = backend.generate_image(prompt, model_id=req.model, quality_mode=qmode)
    except backend.ImageQuotaExhausted as e:
        # The buckets are daily and account-level, so "come back later" without a
        # WHEN leaves the consumer guessing over a window it cannot see. With the
        # deadline it punishes this capability for exactly as long as it is out --
        # and only this capability: the same model still has ~999 chat requests an
        # hour.
        headers = {}
        if e.next_available_epoch is not None:
            headers["Retry-After"] = str(max(0, int(e.next_available_epoch - time.time())))
        raise HTTPException(status_code=429, detail=str(e), headers=headers)
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not images:
        raise HTTPException(
            status_code=503,
            detail="No images returned — the account may not have image generation access",
        )

    return {
        "created": int(time.time()),
        "data": [
            {
                "url":      img["url"],
                "image_id": img.get("image_id"),
                "asset_id": img.get("asset_id"),
                "model":    img.get("model"),
                "mode":     img.get("mode"),
            }
            for img in images
        ],
    }


# ── /grok/skills ─────────────────────────────────────────────────────────────
@app.get("/grok/skills", dependencies=[Depends(verify_key)])
def grok_skills(locale: str = "en-US"):
    """Lista los skills disponibles (Excel, Word, PDF, etc.)."""
    try:
        return {"skills": backend.list_skills(locale)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/settings ────────────────────────────────────────────────────────────
@app.get("/grok/settings", dependencies=[Depends(verify_key)])
def grok_settings():
    """Configuración del usuario en la cuenta de Grok."""
    try:
        return backend.get_user_settings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class SettingsUpdateRequest(BaseModel):
    exclude_from_training: Optional[bool] = None


@app.patch("/grok/settings", dependencies=[Depends(verify_key)])
def grok_update_settings(req: SettingsUpdateRequest):
    """
    Actualiza la configuración del usuario.
    Campos soportados:
      exclude_from_training (bool) — si True, los chats no se usan para entrenar modelos.
    Retorna el estado actual del setting después del update.
    """
    try:
        return backend.set_user_settings(exclude_from_training=req.exclude_from_training)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/modes ───────────────────────────────────────────────────────────────
@app.get("/grok/modes", dependencies=[Depends(verify_key)])
def grok_modes(locale: str = "en-US"):
    """Modos de conversación (fast, auto, expert, heavy, build)."""
    try:
        return backend.list_modes(locale)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/voices ──────────────────────────────────────────────────────────────
@app.get("/grok/voices", dependencies=[Depends(verify_key)])
def grok_voices():
    """Lista de voces disponibles para síntesis de voz."""
    try:
        return backend.list_voices()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/rate-limits/{model_id} ─────────────────────────────────────────────
@app.get("/grok/rate-limits/{model_id:path}", dependencies=[Depends(verify_key)])
def grok_rate_limit(model_id: str, kind: int = 0):
    """Rate limits en tiempo real para un modelo específico."""
    try:
        return backend.get_rate_limit_single(model_id, kind)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/conversations ───────────────────────────────────────────────────────
@app.get("/grok/conversations", dependencies=[Depends(verify_key)])
def grok_list_conversations(limit: int = 20, cursor: str = ""):
    """Lista conversaciones del usuario (título, fecha, ID)."""
    try:
        return backend.list_conversations(limit=limit, cursor=cursor)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/grok/conversations/{conv_id}", dependencies=[Depends(verify_key)])
def grok_get_conversation(conv_id: str):
    """Metadatos de una conversación (título, fechas)."""
    try:
        return backend.get_conversation(conv_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.delete("/grok/conversations/{conv_id}", dependencies=[Depends(verify_key)])
def grok_delete_conversation(conv_id: str):
    """Elimina una conversación permanentemente."""
    try:
        return backend.delete_conversation(conv_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class ConversationUpdateRequest(BaseModel):
    title: str


@app.patch("/grok/conversations/{conv_id}", dependencies=[Depends(verify_key)])
def grok_update_conversation(conv_id: str, req: ConversationUpdateRequest):
    """Renombra una conversación."""
    try:
        return backend.update_conversation(conv_id, req.title)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/grok/conversations/{conv_id}/messages", dependencies=[Depends(verify_key)])
def grok_list_responses(conv_id: str):
    """
    Historial completo de mensajes de una conversación.
    Incluye response_id, rol, texto, parent_id y modelo.
    """
    try:
        msgs = backend.list_responses(conv_id)
        return {"conversation_id": conv_id, "messages": msgs, "count": len(msgs)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class FileUploadRequest(BaseModel):
    filename:  str
    content_b64: Optional[str] = None  # base64-encoded file content
    mime_type:   Optional[str] = None

class AddResponseRequest(BaseModel):
    message: str
    model:   Optional[str] = None
    stream:  bool          = False
    disable_search: Optional[bool] = None
    # Same precedence as ChatRequest.web_search: standard field wins, search
    # is on by default. See resolve_disable_search.
    web_search:     Optional[bool] = None


@app.post("/grok/conversations/{conv_id}/messages", dependencies=[Depends(verify_key)])
async def grok_add_response(conv_id: str, req: AddResponseRequest):
    """
    Añade un mensaje a una conversación existente y obtiene la respuesta.
    Soporta streaming (SSE) igual que /v1/chat/completions.
    """
    model_id = backend.resolve_model(req.model) if req.model else None
    cid = _cid()

    if req.stream:
        async def sse() -> AsyncIterator[str]:
            yield _chunk(cid, req.model or "grok", finish=None)
            try:
                for token in backend.stream_add_response(
                    conv_id, req.message,
                    model_id=model_id,
                    disable_search=resolve_disable_search(req),
                ):
                    yield _chunk(cid, req.model or "grok", token)
            except Exception as e:
                yield _chunk(cid, req.model or "grok", f"\n[Error: {e}]", finish="stop")
                yield "data: [DONE]\n\n"
                return
            yield _chunk(cid, req.model or "grok", finish="stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        try:
            content = backend.complete_add_response(
                conv_id, req.message,
                model_id=model_id,
                disable_search=resolve_disable_search(req),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        return JSONResponse(_full(cid, req.model or "grok", content))


@app.post("/grok/conversations/{conv_id}/share", dependencies=[Depends(verify_key)])
def grok_share_conversation(conv_id: str, resp_id: Optional[str] = None, share_publicly: bool = True):
    """
    Genera un link de compartir para una conversación.
    Si resp_id no se indica, usa la última respuesta del asistente.
    Retorna share_token y share_url.
    """
    try:
        return backend.share_conversation(conv_id, resp_id=resp_id, share_publicly=share_publicly)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/files ───────────────────────────────────────────────────────────────
@app.post("/grok/files", dependencies=[Depends(verify_key)])
def grok_upload_file(req: FileUploadRequest):
    """
    Sube (o registra) un archivo en el storage de Grok.
    Retorna file_id y storage_path que pueden usarse en mensajes futuros.

    Tipos soportados: txt, py, json, html, css, csv, svg, xlsx, pptx, docx, zip, mp3.
    Para imágenes (jpg/png) enviar content_b64 con los bytes reales.
    content_b64: contenido del archivo en base64 (opcional para archivos de texto pequeños).
    """
    content = None
    if req.content_b64:
        try:
            content = base64.b64decode(req.content_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="content_b64 no es base64 válido")
    try:
        return backend.upload_file(req.filename, content=content, mime_type=req.mime_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/memory ──────────────────────────────────────────────────────────────
@app.delete("/grok/memory", dependencies=[Depends(verify_key)])
def grok_delete_memory(conv_id: Optional[str] = None):
    """
    Elimina la memoria de Grok asociada a una conversación específica,
    o la memoria global si no se indica conv_id.
    """
    try:
        return backend.delete_memory(conv_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /grok/suggest ─────────────────────────────────────────────────────────────
@app.get("/grok/suggest", dependencies=[Depends(verify_key)])
def grok_suggest(
    q: str,
    types: list[str] = Query(
        default=["search_completion", "quick_answer", "stock", "grok_completion"]
    ),
):
    """
    Sugerencias en tiempo real para un query (search completions, quick answers, stocks).
    """
    try:
        return {"suggestions": backend.stream_suggestions_collect(q, types)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── /auth/login ───────────────────────────────────────────────────────────────
@app.post("/auth/login")
def login(req: LoginRequest):
    """Login con email y contraseña. Retorna session_token."""
    try:
        token = auth.login_email_password(req.email, req.password)
    except RuntimeError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return {"session_token": token, "hint": "Setear como GROK_SESSION_TOKEN en el .env"}


# ── /auth/otp/send ────────────────────────────────────────────────────────────
@app.post("/auth/otp/send")
def otp_send(req: OtpSendRequest):
    """
    Envía OTP al email. Para cuentas sin contraseña (Twitter/Google OAuth).
    """
    try:
        auth.otp_send(req.email)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "message": f"OTP enviado a {req.email}"}


# ── /auth/otp/verify ──────────────────────────────────────────────────────────
@app.post("/auth/otp/verify")
def otp_verify(req: OtpVerifyRequest):
    """
    Verifica OTP y retorna session_token.
    Acepta '938-612' o '938612'.
    """
    try:
        token = auth.otp_verify(req.email, req.code)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "session_token": token,
        "hint": "Guardá este token como GROK_SESSION_TOKEN en el .env",
    }
