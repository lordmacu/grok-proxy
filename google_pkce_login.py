#!/usr/bin/env python3
"""
Login a Grok via Google OAuth (cross-platform).

Flujo:
  1. GetAuthUrl(GOOGLE) → imprimimos la URL + guardamos oauth2_cookie
  2. Usuario abre la URL en cualquier browser
  3. Google redirige a xai-grok://oauth?code=...&state=...
     - Mac con XAICallback.app registrado: se captura solo en /tmp/
     - Cualquier plataforma: el usuario pega la URL xai-grok://... en el terminal
  4. CreateSession(OAuth2Response(code, state, oauth2_cookie))
     → Grok server hace el exchange con Google, devuelve session_cookie

Sin dependencias extra. Funciona en Mac, Linux, Windows.
"""
import json, time, os, sys, uuid, subprocess, urllib.parse, base64, threading
import httpx, grpc

CALLBACK_FILE = "/tmp/xai_oauth_callback.txt"

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# ── 1. GetAuthUrl en subprocess (evita fork-after-grpc) ─────────────────────
print("Obteniendo URL de Google OAuth...", flush=True)
result = subprocess.run(
    [sys.executable, "-c", """
import grpc, json, uuid
import auth_mgmt_pb2 as m, auth_mgmt_pb2_grpc as g

META = [
    ('user-agent','Grok/1.2.22 (Android; arm64-v8a)'),
    ('x-app-name','Grok Android'),
    ('x-app-version','1.2.22'),
    ('x-app-language','en-US'),
    ('x-xai-request-id', str(uuid.uuid4())),
]
ch = grpc.secure_channel('grok.com:443', grpc.ssl_channel_credentials())
r = g.AuthManagementStub(ch).GetAuthUrl(
    m.GetAuthUrlRequest(provider=m.GOOGLE, redirect_url='xai-grok://oauth'),
    metadata=META,
)
ch.close()
print(json.dumps({'auth_url': r.auth_url, 'oauth2_cookie': r.oauth2_cookie}))
"""],
    capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
)

if result.returncode != 0:
    print("❌ Error GetAuthUrl:", result.stderr[:300])
    sys.exit(1)

data          = json.loads(result.stdout.strip())
auth_url      = data["auth_url"]
oauth2_cookie = data["oauth2_cookie"]

# ── 2. Instrucciones ─────────────────────────────────────────────────────────
print(f"""
{'='*65}
Abrí esta URL en tu browser (con cuenta cgarcialord@gmail.com):

{auth_url}

{'='*65}
Después de elegir la cuenta, el browser intentará abrir una URL
que empieza con  xai-grok://oauth?code=...

  · Mac con XAICallback registrado → se captura automático
  · Cualquier otro caso → copiá esa URL completa y pegala acá

""", flush=True)

# ── 3. Captura: auto (XAICallback) o manual (paste) ─────────────────────────
if os.path.exists(CALLBACK_FILE):
    os.remove(CALLBACK_FILE)

callback_url = None

def wait_for_file():
    global callback_url
    for _ in range(120):
        time.sleep(1)
        if os.path.exists(CALLBACK_FILE):
            callback_url = open(CALLBACK_FILE).read().strip()
            return

file_watcher = threading.Thread(target=wait_for_file, daemon=True)
file_watcher.start()

print("URL de callback (o Enter para esperar auto-captura): ", end="", flush=True)

# Leer stdin sin bloquear indefinidamente
try:
    import select
    rlist, _, _ = select.select([sys.stdin], [], [], 120)
    if rlist:
        line = sys.stdin.readline().strip()
        if line:
            callback_url = line
except (AttributeError, ImportError):
    # Windows no tiene select() para stdin → esperar input directamente
    callback_url = input().strip() or None

if not callback_url:
    # Esperar auto-captura
    file_watcher.join(timeout=120)

if not callback_url:
    print("\n❌ Timeout — no se recibió URL de callback.")
    sys.exit(1)

print(f"\n✅ Callback: {callback_url[:80]}...")

# ── 4. Parsear code y state ──────────────────────────────────────────────────
parsed = urllib.parse.urlparse(callback_url)
params = dict(urllib.parse.parse_qsl(parsed.query))
code  = params.get("code", "")
state = params.get("state", "")

if not code:
    print(f"❌ Sin 'code' en URL: {params}")
    sys.exit(1)

print(f"   code:  {code[:40]}...")
print(f"   state: {state[:40]}...")

# ── 5. CreateSession(OAuth2Response) ─────────────────────────────────────────
import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as mg

ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())

req = m.CreateSessionRequest(
    credentials=m.CreateSessionRequest.Credentials(
        oauth=m.CreateSessionRequest.OAuth2Response(
            code=code,
            state=state,
            oauth2_cookie=oauth2_cookie,
        )
    ),
)

def try_create_session(path, stub_cls=None):
    """Intenta CreateSession en el path dado."""
    if stub_cls:
        stub = stub_cls(ch)
        return stub.CreateSession(req, metadata=GROK_META)
    else:
        method = ch.unary_unary(
            path,
            request_serializer=m.CreateSessionRequest.SerializeToString,
            response_deserializer=m.CreateSessionResponse.FromString,
        )
        return method(req, metadata=GROK_META)

def save_session(resp, label=""):
    token = resp.session_cookie
    uid   = resp.session.user_id if resp.HasField("session") else ""
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": uid}, f, indent=2)
        print(f"\n✅ [{label}] Sesión guardada en .session.json")
        print(f"   session_cookie: {token[:60]}…")
        print(f"   user_id: {uid}")
        return True
    else:
        raw = resp.SerializeToString()
        print(f"⚠️  [{label}] Sin token. Raw ({len(raw)} bytes): {raw.hex()}")
        return False

# Intentar auth_mgmt primero, luego auth_frontend
for path, label in [
    ("/auth_mgmt.AuthManagement/CreateSession",  "auth_mgmt"),
    ("/auth_frontend.AuthFrontend/CreateSession", "auth_frontend"),
]:
    print(f"\nProbando {label}...", flush=True)
    try:
        resp = try_create_session(path)
        if save_session(resp, label):
            break
    except grpc.RpcError as e:
        print(f"❌ {label}: {e.code()} — {e.details()}")
    except Exception as e:
        print(f"❌ {label}: {e}")

ch.close()
