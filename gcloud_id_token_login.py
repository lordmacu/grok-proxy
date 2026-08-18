#!/usr/bin/env python3
"""
Obtiene un Google ID token via localhost (sin custom scheme, sin gcloud CLI).
Usa las credenciales públicas del gcloud SDK (cliente "installed", acepta localhost).
Luego llama auth_frontend.AuthFrontend/CreateSession con IdentityTokenResponse.
"""
import secrets, hashlib, base64, json, urllib.parse, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx, grpc, auth_mgmt_pb2 as m

# Credenciales públicas del gcloud SDK (open source, aceptan localhost)
CLIENT_ID = "764086051850-6qr4p6gpi6hvbin706g5s7a1bh74aqb4.apps.googleusercontent.com"
CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
REDIRECT_URI  = "http://localhost:8765"

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# ── PKCE ─────────────────────────────────────────────────────────────────────
verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode()).digest()
).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)

auth_url = (
    "https://accounts.google.com/o/oauth2/v2/auth?"
    + urllib.parse.urlencode({
        "response_type":         "code",
        "client_id":             CLIENT_ID,
        "redirect_uri":          REDIRECT_URI,
        "scope":                 "openid email profile",
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "state":                 state,
        "login_hint":            "cgarcialord@gmail.com",
    })
)

# ── HTTP server local ─────────────────────────────────────────────────────────
captured: dict = {}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        captured.update(params)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK - podes cerrar esta ventana</h1>")
    def log_message(self, *a): pass

server = HTTPServer(("localhost", 8765), Handler)

print(f"\n{'='*65}")
print("Abrí esta URL en Chrome:")
print()
print(auth_url)
print(f"{'='*65}")
print("\nEsperando redirect en localhost:8765...", flush=True)

while "code" not in captured:
    server.handle_request()

server.server_close()

code = captured.get("code", "")
print(f"\n✅ Code capturado: {code[:40]}...")

# ── Exchange code → tokens ───────────────────────────────────────────────────
r = httpx.post(
    "https://oauth2.googleapis.com/token",
    data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": verifier,
        "grant_type":    "authorization_code",
    },
)
tdata = r.json()
id_token = tdata.get("id_token", "")

if not id_token:
    print(f"❌ Sin id_token: {tdata}")
    exit(1)

# Mostrar claims del JWT
payload = json.loads(base64.urlsafe_b64decode(id_token.split(".")[1] + "===="))
print(f"✅ id_token — email: {payload.get('email')}, aud: {payload.get('aud','')[:70]}")

# ── auth_frontend.AuthFrontend/CreateSession ──────────────────────────────────
print("\nLlamando auth_frontend.AuthFrontend/CreateSession...")

ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
req = m.CreateSessionRequest(
    credentials=m.CreateSessionRequest.Credentials(
        identity_token_response=m.CreateSessionRequest.IdentityTokenResponse(
            provider=m.GOOGLE_ANDROID,
            identity_token=id_token,
        )
    ),
    prompt_on_duplicate_email=True,
)
method = ch.unary_unary(
    "/auth_frontend.AuthFrontend/CreateSession",
    request_serializer=m.CreateSessionRequest.SerializeToString,
    response_deserializer=m.CreateSessionResponse.FromString,
)

try:
    resp  = method(req, metadata=GROK_META)
    token = resp.session_cookie
    uid   = resp.session.user_id if resp.HasField("session") else ""
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": uid}, f, indent=2)
        print(f"\n✅ Sesión guardada en .session.json")
        print(f"   cookie: {token[:60]}…")
        print(f"   uid:    {uid}")
    else:
        raw = resp.SerializeToString()
        print(f"⚠️  Sin token. raw ({len(raw)} bytes): {raw.hex()}")
        print(f"   Proto: {resp}")
except grpc.RpcError as e:
    print(f"❌ gRPC: {e.code()} — {e.details()}")
finally:
    ch.close()
