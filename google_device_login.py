#!/usr/bin/env python3
"""
Login via Google ID Token usando Device Flow.
El usuario entra un código corto en google.com/device — sin redirect.

Luego llama auth_frontend.AuthFrontend/CreateSession con IdentityTokenResponse
(sin Castle, así usa el APK).
"""
import time, json, uuid, sys
import httpx, grpc

import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as mg

# Google client ID del APK de Grok
GOOGLE_CLIENT_ID = "455662147616-fai32tqkrc0mqkouoe6l0suk287lkb1k.apps.googleusercontent.com"
SCOPE = "openid email profile"

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# ── 1. Iniciar Device Flow ───────────────────────────────────────────────────
print("Iniciando Google Device Flow...")
r = httpx.post(
    "https://oauth2.googleapis.com/device/code",
    data={"client_id": GOOGLE_CLIENT_ID, "scope": SCOPE},
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

if r.status_code != 200:
    print(f"\n❌ Device Flow no soportado con este client_id.")
    print("Intentando con scope alternativo...")
    r = httpx.post(
        "https://accounts.google.com/o/oauth2/device/code",
        data={"client_id": GOOGLE_CLIENT_ID, "scope": SCOPE},
    )
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    if r.status_code != 200:
        print("❌ Device Flow no funciona con el client_id del APK.")
        sys.exit(1)

data = r.json()
device_code      = data["device_code"]
user_code        = data["user_code"]
verification_url = data.get("verification_url", data.get("verification_uri", ""))
interval         = data.get("interval", 5)
expires_in       = data.get("expires_in", 1800)

print(f"\n{'='*50}")
print(f"Abrí esta URL en el browser: {verification_url}")
print(f"Ingresá este código: {user_code}")
print(f"{'='*50}")
print(f"\nEsperando que autorices (expira en {expires_in}s)...")

# ── 2. Polling para obtener el token ─────────────────────────────────────────
id_token = None
for _ in range(expires_in // interval + 1):
    time.sleep(interval)
    tr = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    tdata = tr.json()
    if "id_token" in tdata:
        id_token = tdata["id_token"]
        print(f"\n✅ Google ID token obtenido.")
        break
    elif tdata.get("error") == "authorization_pending":
        print(".", end="", flush=True)
    elif tdata.get("error") == "slow_down":
        interval += 5
    else:
        print(f"\n❌ Error: {tdata}")
        break

if not id_token:
    print("\n❌ No se obtuvo el ID token.")
    sys.exit(1)

# ── 3. auth_frontend.CreateSession con IdentityTokenResponse ─────────────────
print("\nLlamando auth_frontend.AuthFrontend/CreateSession...")

# Necesitamos registrar el método en el proto
# Usamos el canal de grok.com pero el path es /auth_frontend.AuthFrontend/CreateSession
ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())

# Construir el request manualmente (proto3 encoding)
# auth_frontend.CreateSessionRequest:
#   tag 1 = credentials (auth_mgmt.CreateSessionRequest.Credentials)
#     tag 4 = identity_token_response (IdentityTokenResponse)
#       tag 1 = provider (GOOGLE_ANDROID = 6)
#       tag 2 = identity_token (string)
#   tag 8 = prompt_on_duplicate_email (bool, true)

import struct

def encode_varint(value):
    bits = value & 0x7F
    value >>= 7
    result = b''
    while value:
        result += bytes([0x80 | bits])
        bits = value & 0x7F
        value >>= 7
    result += bytes([bits])
    return result

def encode_string(value):
    encoded = value.encode('utf-8')
    return encode_varint(len(encoded)) + encoded

def encode_field(field_num, wire_type, value_bytes):
    tag = (field_num << 3) | wire_type
    return encode_varint(tag) + value_bytes

# IdentityTokenResponse: provider=GOOGLE_ANDROID(6), identity_token=id_token
identity_resp = (
    encode_field(1, 0, encode_varint(6)) +           # provider = GOOGLE_ANDROID
    encode_field(2, 2, encode_string(id_token))      # identity_token
)

# Credentials: identity_token_response at tag 4
credentials = encode_field(4, 2, encode_varint(len(identity_resp)) + identity_resp)

# CreateSessionRequest: credentials at tag 1, prompt_on_duplicate_email at tag 8
req_bytes = (
    encode_field(1, 2, encode_varint(len(credentials)) + credentials) +
    encode_field(8, 0, encode_varint(1))  # prompt_on_duplicate_email = true
)

# gRPC frame: 1 byte compression flag + 4 bytes length + proto bytes
frame = b'\x00' + struct.pack('>I', len(req_bytes)) + req_bytes

# Llamada raw gRPC via http2
try:
    from grpc import experimental
    resp = ch.unary_unary(
        '/auth_frontend.AuthFrontend/CreateSession',
        request_serializer=None,
        response_deserializer=None,
    )(req_bytes, metadata=GROK_META)
    print(f"Response: {resp}")
except Exception as e:
    print(f"Error raw: {e}")

# Intentar con el stub de auth_mgmt (mismo proto, diferente path)
# Reusamos CreateSessionRequest del proto pero llamamos a auth_frontend
try:
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
        '/auth_frontend.AuthFrontend/CreateSession',
        request_serializer=m.CreateSessionRequest.SerializeToString,
        response_deserializer=m.CreateSessionResponse.FromString,
    )
    resp = method(req, metadata=GROK_META)
    print(f"\nResponse: {resp}")
    raw = resp.SerializeToString()
    print(f"bytes ({len(raw)}): {raw.hex()}")
    token = resp.session_cookie or (resp.session.session_id if resp.HasField("session") else "")
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": resp.session.user_id}, f, indent=2)
        print(f"\n✅ Sesión guardada: {token[:60]}…")
    else:
        print(f"⚠️  Sin token. Full proto: {resp}")
except grpc.RpcError as e:
    print(f"❌ gRPC error: {e.code()} — {e.details()}")
except Exception as e:
    print(f"❌ Error: {e}")

ch.close()
