#!/usr/bin/env python3
"""
Paso 2: toma la URL de callback xai-grok://oauth?code=...&state=... y crea la sesión.
Uso: python3 step2_finish_login.py 'xai-grok://oauth?code=...&state=...'
"""
import sys, json, uuid, urllib.parse, grpc
import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

callback_url = sys.argv[1] if len(sys.argv) > 1 else input("Pegá la URL de callback: ").strip()

parsed = urllib.parse.urlparse(callback_url)
params = dict(urllib.parse.parse_qsl(parsed.query))
code   = params.get("code", "")
state  = params.get("state", "")

with open(".oauth_state.json") as f:
    saved = json.load(f)
oauth2_cookie = saved["oauth2_cookie"]

print(f"code  : {code!r}")
print(f"state : {state!r}")

ch   = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
stub = g.AuthManagementStub(ch)

meta = list(META)
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
    print(f"bytes ({len(raw)}): {raw.hex() or '(vacío)'}")
    cookie = resp.session_cookie
    sid    = resp.session.session_id if resp.HasField("session") else ""
    token  = cookie or sid
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": resp.session.user_id}, f, indent=2)
        print(f"✅ Sesión guardada: {token[:50]}…")
    else:
        print("⚠️  Sin token en la respuesta. Raw:", raw.hex())
except grpc.RpcError as e:
    print(f"❌ {e.code()} — {e.details()}")

ch.close()
