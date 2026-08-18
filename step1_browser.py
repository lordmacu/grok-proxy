#!/usr/bin/env python3
"""
Abre Chrome con tu perfil existente, navega a Twitter OAuth,
intercepta el redirect xai-grok://oauth?code=... antes de que falle,
y llama a CreateSession automáticamente.
"""
import json, uuid, urllib.parse, grpc
from playwright.sync_api import sync_playwright

import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

CHROME_PROFILE = "/Users/cristian/Library/Application Support/Google/Chrome"

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# Leer la URL y cookie guardadas en el paso anterior
with open(".oauth_state.json") as f:
    saved = json.load(f)

auth_url     = saved["auth_url"]
oauth2_cookie = saved["oauth2_cookie"]

print(f"Usando auth_url: {auth_url[:80]}...")
print(f"oauth2_cookie: {oauth2_cookie[:40]}...")

captured = {}

def handle_request(request):
    if request.url.startswith("xai-grok://"):
        print(f"\n✅ Redirect capturado: {request.url}")
        captured["url"] = request.url
        # No podemos abortar desde aquí, pero ya tenemos la URL

with sync_playwright() as p:
    # Usar Chrome del sistema con el perfil real (tiene la sesión de Twitter)
    browser = p.chromium.launch_persistent_context(
        user_data_dir=CHROME_PROFILE,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        headless=False,
        args=["--no-first-run", "--no-default-browser-check"],
    )

    page = browser.new_page()

    # Interceptar cualquier navegación antes de que el browser intente cargarla
    def on_route(route):
        url = route.request.url
        if url.startswith("xai-grok://"):
            print(f"\n✅ Redirect interceptado: {url}")
            captured["url"] = url
            route.abort()  # evita el error de "scheme desconocido"
        else:
            route.continue_()

    page.route("**", on_route)

    print("\nAbriendo Twitter en Chrome...")
    try:
        page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  (goto error ignorable: {e})")

    if not captured:
        # Esperar a que el usuario autorice manualmente si no está auto-logueado
        print("\nEsperando autorización en Twitter (hacé click en 'Authorize app')...")
        print("Tenés 120 segundos...")
        try:
            page.wait_for_url("xai-grok://**", timeout=120000)
        except Exception:
            pass

    browser.close()

if not captured.get("url"):
    print("❌ No se capturó el callback. Intentá de nuevo.")
    exit(1)

# Parsear code y state del callback
parsed = urllib.parse.urlparse(captured["url"])
params = dict(urllib.parse.parse_qsl(parsed.query))
code  = params.get("code", "")
state = params.get("state", "")
print(f"code : {code[:30]}...")
print(f"state: {state[:30]}...")

# CreateSession
print("\nCreando sesión en Grok...")
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
    print(f"Response bytes ({len(raw)}): {raw.hex() or '(vacío)'}")
    cookie = resp.session_cookie
    sid    = resp.session.session_id if resp.HasField("session") else ""
    token  = cookie or sid
    if token:
        with open(".session.json", "w") as f:
            json.dump({"session_cookie": token, "user_id": resp.session.user_id}, f, indent=2)
        print(f"\n✅ Sesión guardada: {token[:60]}…")
    else:
        print(f"\n⚠️  Sin token. Proto completo: {resp}")
except grpc.RpcError as e:
    print(f"❌ CreateSession: {e.code()} — {e.details()}")

ch.close()
