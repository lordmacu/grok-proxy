#!/usr/bin/env python3
"""
Prueba cuáles endpoints de Grok son accesibles con credenciales anónimas.
Usa la cuenta guardada en .anon_creds.json (no crea cuentas nuevas).
"""
import base64, hashlib, json, os, sys, time, uuid
from dataclasses import dataclass, field
from typing import Optional

import coincurve, grpc
import auth_frontend_pb2 as auth_pb
import auth_frontend_pb2_grpc as auth_grpc
import grok_api_pb2 as api_pb
import grok_api_pb2_grpc as api_grpc

HOST = "grok.com"
PORT = 443
CREDS_FILE = os.path.join(os.path.dirname(__file__), ".anon_creds.json")

BASE_META = [
    ("user-agent",     "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",     "Grok Android"),
    ("x-app-version",  "1.2.22"),
    ("x-app-language", "en-US"),
]

ANSI = {
    "ok":   "\033[92m",   # verde
    "err":  "\033[91m",   # rojo
    "warn": "\033[93m",   # amarillo
    "head": "\033[1;96m", # cyan negrita
    "dim":  "\033[2m",
    "rst":  "\033[0m",
}


def rid():
    return str(uuid.uuid4())


def make_channel():
    return grpc.secure_channel(f"{HOST}:{PORT}", grpc.ssl_channel_credentials())


# ── Auth ──────────────────────────────────────────────────────────────────────

def _der_to_compact(der):
    assert der[0] == 0x30 and der[2] == 0x02
    r_len = der[3]
    r = der[4:4 + r_len].lstrip(b'\x00')
    rest = der[4 + r_len:]
    assert rest[0] == 0x02
    s = rest[2:2 + rest[1]].lstrip(b'\x00')
    return r.rjust(32, b'\x00') + s.rjust(32, b'\x00')


def load_creds(channel):
    if not os.path.exists(CREDS_FILE):
        print(f"{ANSI['err']}No hay credenciales guardadas. Ejecuta grok_client.py primero.{ANSI['rst']}")
        sys.exit(1)
    with open(CREDS_FILE) as f:
        creds = json.load(f)
    return creds


def refresh_creds(channel, creds):
    stub = auth_grpc.AuthFrontendStub(channel)
    privkey = bytes.fromhex(creds["privkey"])
    challenge = stub.CreateAnonUserChallenge(
        auth_pb.CreateChallengeRequest(anon_user_id=creds["anon_user_id"]),
        metadata=BASE_META + [("x-xai-request-id", rid())],
    ).challenge
    digest = hashlib.sha256(challenge).digest()
    der = coincurve.PrivateKey(privkey).sign(digest, hasher=None)
    sig = _der_to_compact(der)
    creds = dict(creds)
    creds["challenge_b64"] = base64.b64encode(challenge).decode()
    creds["signature_b64"] = base64.b64encode(sig).decode()
    with open(CREDS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    print(f"  {ANSI['warn']}[auth] challenge refrescado{ANSI['rst']}")
    return creds


def auth_meta(creds):
    return BASE_META + [
        ("x-xai-request-id", rid()),
        ("x-anonuserid",     creds["anon_user_id"]),
        ("x-challenge",      creds["challenge_b64"]),
        ("x-signature",      creds["signature_b64"]),
    ]


# ── Probe runner ─────────────────────────────────────────────────────────────

@dataclass
class Result:
    name:    str
    ok:      bool
    code:    Optional[str] = None
    detail:  str = ""
    data:    str = ""


def probe(name: str, fn, channel, creds, max_auth_retry=1):
    """Llama fn(channel, creds) → Result. Refresca auth una vez si UNAUTHENTICATED."""
    for attempt in range(max_auth_retry + 1):
        try:
            data = fn(channel, creds)
            return Result(name=name, ok=True, data=str(data)[:200])
        except grpc.RpcError as e:
            code = e.code().name
            detail = e.details() or ""
            if code == "UNAUTHENTICATED" and attempt < max_auth_retry:
                creds = refresh_creds(channel, creds)
                continue
            return Result(name=name, ok=False, code=code, detail=detail[:120])
        except Exception as e:
            return Result(name=name, ok=False, code="EXCEPTION", detail=str(e)[:120])


def print_result(r: Result):
    if r.ok:
        sym = f"{ANSI['ok']}✅{ANSI['rst']}"
        extra = f" {ANSI['dim']}{r.data}{ANSI['rst']}" if r.data else ""
    else:
        sym = f"{ANSI['err']}❌{ANSI['rst']}"
        extra = f" {ANSI['warn']}{r.code}{ANSI['rst']} — {r.detail}"
    print(f"  {sym} {r.name}{extra}")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_models(ch, creds):
    stub = api_grpc.ModelsStub(ch)
    resp = stub.GetModels(api_pb.EmptyRequest(), metadata=auth_meta(creds))
    names = [m.model_id for m in resp.models]
    return f"{len(names)} modelos: {names}"


def test_list_modes(ch, creds):
    stub = api_grpc.ModelsStub(ch)
    resp = stub.ListModes(api_pb.ListModesRequest(locale="en-US"), metadata=auth_meta(creds))
    names = [m.display_name or m.mode_id for m in resp.modes]
    return f"{len(names)} modos: {names[:5]}"


def test_get_imagine_overrides(ch, creds):
    stub = api_grpc.ModelsStub(ch)
    resp = stub.GetModelsImagineOverrides(api_pb.EmptyRequest(), metadata=auth_meta(creds))
    return repr(resp)[:120]


def test_conversation_starters(ch, creds):
    stub = api_grpc.ChatStub(ch)
    resp = stub.GetConversationStarters(api_pb.EmptyRequest(), metadata=auth_meta(creds))
    return f"{len(resp.starters)} starters: {resp.starters[:3]}"


def test_rate_limits_default(ch, creds):
    stub = api_grpc.RateLimitsStub(ch)
    resp = stub.GetRateLimits(
        api_pb.GetRateLimitsRequest(request_kind=api_pb.DEFAULT, model_name="grok-3"),
        metadata=auth_meta(creds),
    )
    return (f"remaining={resp.remaining_queries}/{resp.total_queries} "
            f"window={resp.window_size_seconds}s")


def test_rate_limits_reasoning(ch, creds):
    stub = api_grpc.RateLimitsStub(ch)
    resp = stub.GetRateLimits(
        api_pb.GetRateLimitsRequest(request_kind=api_pb.REASONING, model_name="grok-3"),
        metadata=auth_meta(creds),
    )
    return f"remaining={resp.remaining_queries}/{resp.total_queries}"


def test_get_user_settings(ch, creds):
    stub = api_grpc.SettingsStub(ch)
    resp = stub.GetUserSettings(api_pb.EmptyRequest(), metadata=auth_meta(creds))
    return repr(resp)[:150]


def test_list_skills(ch, creds):
    stub = api_grpc.SkillsStub(ch)
    resp = stub.ListSkills(api_pb.ListSkillsRequest(locale="en-US"), metadata=auth_meta(creds))
    names = [s.display_name or s.skill_id for s in resp.skills]
    return f"{len(names)} skills: {names[:5]}"


def test_list_top_voices(ch, creds):
    stub = api_grpc.VoiceStub(ch)
    resp = stub.ListTopVoices(api_pb.ListTopVoicesRequest(limit=5), metadata=auth_meta(creds))
    names = [v.display_name or v.voice_id for v in resp.voices]
    return f"{len(resp.voices)} voices: {names[:5]}"


def test_list_voices(ch, creds):
    stub = api_grpc.VoiceStub(ch)
    resp = stub.ListVoices(api_pb.ListVoicesRequest(limit=5), metadata=auth_meta(creds))
    return f"{len(resp.voices)} voices"


def test_search_voices(ch, creds):
    stub = api_grpc.VoiceStub(ch)
    resp = stub.SearchVoices(api_pb.SearchVoicesRequest(query="news"), metadata=auth_meta(creds))
    return f"{len(resp.voices)} voices"


def test_find_stock(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.FindStock(api_pb.FindStockRequest(ticker="AAPL"), metadata=auth_meta(creds))
    tickers = [s.ticker for s in resp.stock_summaries]
    return f"{len(resp.stock_summaries)} results: {tickers}"


def test_get_stock_mini(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.GetStockMiniSummary(
        api_pb.GetStockTickerRequest(ticker="AAPL"),
        metadata=auth_meta(creds),
    )
    return f"ticker={resp.ticker} ts={resp.timestamp}"


def test_get_stock_summary(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.GetStockSummary(
        api_pb.GetStockTickerRequest(ticker="AAPL"),
        metadata=auth_meta(creds),
    )
    return f"ticker={resp.ticker} mktcap={resp.market_cap:.2e}"


def test_get_stock_chart(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.GetStockChartData(
        api_pb.GetStockChartDataRequest(ticker="AAPL", timespan="1D"),
        metadata=auth_meta(creds),
    )
    return f"ticker={resp.ticker} unit={resp.unit}"


def test_get_related_tickers(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.GetRelatedTickers(
        api_pb.GetStockTickerRequest(ticker="AAPL"),
        metadata=auth_meta(creds),
    )
    return f"related: {list(resp.tickers)[:5]}"


def test_get_stock_financials(ch, creds):
    stub = api_grpc.FinanceStub(ch)
    resp = stub.GetStockFinancials(
        api_pb.GetStockTickerRequest(ticker="AAPL"),
        metadata=auth_meta(creds),
    )
    return repr(resp)[:100]


def test_stream_suggestions(ch, creds):
    stub = api_grpc.SuggestionServiceStub(ch)
    # AcceptedSuggestionType es un message con type + max_items
    accepted = [
        api_pb.AcceptedSuggestionType(
            type=api_pb.SUGGESTION_TYPE_SEARCH_COMPLETION, max_items=3),
        api_pb.AcceptedSuggestionType(
            type=api_pb.SUGGESTION_TYPE_GROK_COMPLETION, max_items=3),
        api_pb.AcceptedSuggestionType(
            type=api_pb.SUGGESTION_TYPE_STOCK, max_items=3),
    ]
    chunks = []
    for chunk in stub.StreamSuggestions(
        api_pb.StreamSuggestionsRequest(query="AAPL stock", accepted_types=accepted),
        metadata=auth_meta(creds),
    ):
        which = chunk.WhichOneof("suggestion")
        if which:
            val = getattr(chunk, which)
            chunks.append(f"{which}:{getattr(val, 'text', '') or getattr(val, 'ticker', '')}")
        if len(chunks) >= 6:
            break
    return f"{len(chunks)} sugerencias: {chunks}"


# ── Main ──────────────────────────────────────────────────────────────────────

SUITES = [
    ("Models",  [
        ("GetModels",                test_get_models),
        ("ListModes",                test_list_modes),
        ("GetModelsImagineOverrides", test_get_imagine_overrides),
    ]),
    ("Chat", [
        ("GetConversationStarters",  test_conversation_starters),
    ]),
    ("RateLimits", [
        ("GetRateLimits(DEFAULT/grok-3)",   test_rate_limits_default),
        ("GetRateLimits(REASONING/grok-3)", test_rate_limits_reasoning),
    ]),
    ("Settings", [
        ("GetUserSettings",          test_get_user_settings),
    ]),
    ("Skills", [
        ("ListSkills",               test_list_skills),
    ]),
    ("Voice", [
        ("ListTopVoices",            test_list_top_voices),
        ("ListVoices",               test_list_voices),
        ("SearchVoices",             test_search_voices),
    ]),
    # Finance → 404 en grok.com (no desplegado en el app standalone)
    # ("Finance", [...]),
    ("SuggestionService", [
        ("StreamSuggestions",        test_stream_suggestions),
    ]),
]


def main():
    with make_channel() as ch:
        creds = load_creds(ch)
        print(f"\n{ANSI['head']}Cuenta anónima: {creds['anon_user_id']}{ANSI['rst']}\n")

        results = []
        for suite_name, tests in SUITES:
            print(f"{ANSI['head']}── {suite_name} ──{ANSI['rst']}")
            for name, fn in tests:
                r = probe(name, fn, ch, creds)
                if r.ok:
                    creds_used = creds  # puede haberse refrescado dentro de probe
                print_result(r)
                results.append(r)
                time.sleep(0.3)  # pequeña pausa cortés entre llamadas
            print()

    ok  = [r for r in results if r.ok]
    err = [r for r in results if not r.ok]

    print(f"{ANSI['head']}Resumen: {ANSI['ok']}{len(ok)} OK{ANSI['rst']} / {ANSI['err']}{len(err)} fallidos{ANSI['rst']}")
    if err:
        print()
        print(f"{ANSI['warn']}Fallidos:{ANSI['rst']}")
        for r in err:
            print(f"  {r.name}: {r.code} — {r.detail}")


if __name__ == "__main__":
    main()
