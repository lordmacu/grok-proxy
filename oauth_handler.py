#!/usr/bin/env python3
"""
Login completo: abre Chrome con Twitter OAuth, intercepta xai-grok:// via handler nativo,
crea la sesión de Grok automáticamente.

Requiere haber corrido antes:
  osacompile -o /tmp/XAICallback.app /tmp/xai_url_handler.applescript
  lsregister -f /tmp/XAICallback.app
"""
import json, uuid, time, subprocess, urllib.parse, os, sys

CALLBACK_FILE = "/tmp/xai_oauth_callback.txt"

# Limpiar callback previo
if os.path.exists(CALLBACK_FILE):
    os.remove(CALLBACK_FILE)

# ── 1. GetAuthUrl (en subprocess separado para evitar fork-after-grpc) ───────
print("Obteniendo URL de autorización de Grok...", flush=True)
result = subprocess.run(
    [sys.executable, "-c", """
import grpc, json, uuid
import auth_mgmt_pb2 as m, auth_mgmt_pb2_grpc as g
META = [
    ("user-agent","Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name","Grok Android"),
    ("x-app-version","1.2.22"),
    ("x-app-language","en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]
ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
r  = g.AuthManagementStub(ch).GetAuthUrl(
    m.GetAuthUrlRequest(provider=m.X_ANDROID, redirect_url="xai-grok://oauth"),
    metadata=META,
)
print(json.dumps({"auth_url": r.auth_url, "oauth2_cookie": r.oauth2_cookie}))
ch.close()
"""],
    capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
)
if result.returncode != 0:
    print("Error obteniendo auth_url:", result.stderr[:500])
    exit(1)

data          = json.loads(result.stdout.strip())
auth_url      = data["auth_url"]
oauth2_cookie = data["oauth2_cookie"]
print("OK.", flush=True)

# ── 2. Abrir en Chrome (antes de importar grpc para evitar FD warnings) ──────
print(f"\nAbriendo en Chrome...", flush=True)
subprocess.Popen(["open", "-a", "Google Chrome", auth_url],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

import grpc
import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# ── 3. Esperar el callback ───────────────────────────────────────────────────
print("Hacé click en 'Authorize app' en Twitter.")
print("Esperando callback (máx 120s)...", end="", flush=True)
for _ in range(120):
    time.sleep(1)
    print(".", end="", flush=True)
    if os.path.exists(CALLBACK_FILE):
        break
else:
    print("\n❌ Timeout sin callback.")
    ch.close()
    exit(1)

print()
callback_url = open(CALLBACK_FILE).read().strip()
print(f"✅ Callback capturado: {callback_url[:80]}...")

parsed = urllib.parse.urlparse(callback_url)
params = dict(urllib.parse.parse_qsl(parsed.query))
code   = params.get("code", "")
state  = params.get("state", "")

if not code:
    print(f"❌ No hay 'code' en el callback: {params}")
    ch.close()
    exit(1)

print(f"code : {code[:40]}...")
print(f"state: {state[:40]}...")

# ── 4. CreateSession ─────────────────────────────────────────────────────────
print("\nCreando sesión en Grok...")
meta = list(GROK_META)
meta.append(("cookie", oauth2_cookie))

req = m.CreateSessionRequest(
    credentials=m.CreateSessionRequest.Credentials(
        oauth=m.CreateSessionRequest.OAuth2Response(
            code=code,
            state=state,
            oauth2_cookie=oauth2_cookie,
        )
    ),
)

try:
    resp = stub.CreateSession(req, metadata=meta)
    raw  = resp.SerializeToString()
    print(f"Response bytes ({len(raw)}): {raw.hex() or '(vacío)'}")
    token = resp.session_cookie or (resp.session.session_id if resp.HasField("session") else "")
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": resp.session.user_id}, f, indent=2)
        print(f"\n✅ Sesión guardada en .session.json")
        print(f"   token: {token[:60]}…")
    else:
        print(f"\n⚠️  Sin token. Proto: {resp}")
except grpc.RpcError as e:
    print(f"❌ CreateSession: {e.code()} — {e.details()}")

ch.close()
