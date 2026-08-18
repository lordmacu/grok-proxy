#!/usr/bin/env python3
"""
Muestra el rate limit actual de todos los modelos con límite alto.

Correr:
  python3 demo_rate_check.py
"""
import grpc, uuid, json, os

import grok_api_pb2 as ga

SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session.json")

token = json.load(open(SESSION_FILE))["session_cookie"]

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
    ("cookie",           f"sso={token}; sso-rw={token}"),
]

ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
rl = ch.unary_unary(
    "/grok_api.RateLimits/GetRateLimits",
    request_serializer=ga.GetRateLimitsRequest.SerializeToString,
    response_deserializer=ga.GetRateLimitsResponse.FromString,
)

MODELOS = [
    # Normales (referencia)
    "grok-3",
    "grok-4",
    # Companions (10/hora)
    "grok-3-mini-companion",
    "grok-4-1-non-thinking-companion",
    # 999/hora
    "imagine-agent-mode",
    "imagine-agent-mode-dev",
    "imagine-agent-mode-grok-4-5",
    "grok-plugins-4p6-excel",
    "grok-plugins-4p6-word",
    "grok-plugins-4p6-docs",
    "grok-plugins-4p6-sheets",
    "grok-plugins-4p6-powerpoint",
    "grok-plugins-4p6-outlook",
    "grok-plugins-4p6-slides",
]

print(f"\n{'Modelo':<45} {'Restantes':>10} {'Total':>7} {'Ventana':>8}")
print("─" * 75)
for m in MODELOS:
    try:
        r = rl(ga.GetRateLimitsRequest(model_name=m, request_kind=0), metadata=META)
        if r.total_queries == 0:
            print(f"{m:<45} {'N/A':>10}")
            continue
        win = f"{r.window_size_seconds//3600}h" if r.window_size_seconds >= 3600 else f"{r.window_size_seconds}s"
        bar_fill = int(r.remaining_queries / r.total_queries * 20)
        bar = "█" * bar_fill + "░" * (20 - bar_fill)
        print(f"{m:<45} {r.remaining_queries:>10}/{r.total_queries:<6} {win:>6}  [{bar}]")
    except grpc.RpcError as e:
        print(f"{m:<45} ERR: {e.details()[:40]}")

ch.close()
print()
