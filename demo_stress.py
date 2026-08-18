#!/usr/bin/env python3
"""
Manda N requests seguidos al modelo de 999/hora y muestra cuánto queda.
Sirve para confirmar que el límite es real y que el contador baja.

Correr:
  python3 demo_stress.py        # 5 requests por defecto
  python3 demo_stress.py 20     # 20 requests
"""
import grpc, uuid, json, sys, os, time

import grok_api_pb2 as ga
import grok_api_pb2_grpc as gg

SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session.json")
MODEL        = "imagine-agent-mode"

token = json.load(open(SESSION_FILE))["session_cookie"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def make_meta():
    return [
        ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
        ("x-app-name",       "Grok Android"),
        ("x-app-version",    "1.2.22"),
        ("x-app-language",   "en-US"),
        ("x-xai-request-id", str(uuid.uuid4())),
        ("cookie",           f"sso={token}; sso-rw={token}"),
    ]


def get_remaining():
    ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
    rl = ch.unary_unary(
        "/grok_api.RateLimits/GetRateLimits",
        request_serializer=ga.GetRateLimitsRequest.SerializeToString,
        response_deserializer=ga.GetRateLimitsResponse.FromString,
    )
    r = rl(ga.GetRateLimitsRequest(model_name=MODEL, request_kind=0), metadata=make_meta())
    ch.close()
    return r.remaining_queries, r.total_queries


def chat_one(i):
    ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
    stub = gg.ChatStub(ch)
    req = ga.CreateConversationAndRespondRequest(
        model_name=MODEL,
        message=f"Reply with exactly: 'Request {i} OK'",
        disable_search=True,
    )
    tokens = []
    for chunk in stub.CreateConversationAndRespond(req, metadata=make_meta(), timeout=30):
        if chunk.HasField("add_response") and chunk.add_response.token:
            tokens.append(chunk.add_response.token)
    ch.close()
    return "".join(tokens).replace("Thinking about your request", "").strip()


antes_rem, total = get_remaining()
print(f"Antes:  {antes_rem}/{total} restantes en {MODEL}\n")
print(f"Mandando {N} requests...\n")

t0 = time.time()
for i in range(1, N + 1):
    resp = chat_one(i)
    print(f"  [{i}/{N}] {resp[:60]}")

elapsed = time.time() - t0
despues_rem, _ = get_remaining()

print(f"\nDespués: {despues_rem}/{total} restantes")
print(f"Usados:  {antes_rem - despues_rem} en {elapsed:.1f}s ({elapsed/N:.1f}s/request)")
