#!/usr/bin/env python3
"""
Login via Twitter OAuth usando AppleScript para automatizar Chrome.
No necesita extensiones ni modificar el perfil de Chrome.
"""
import json, uuid, time, subprocess, urllib.parse, grpc

import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

def run_applescript(script):
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    return result.stdout.strip(), result.stderr.strip()

# ── 1. Obtener URL fresca ────────────────────────────────────────────────────
print("Obteniendo auth_url de Grok...")
ch   = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
stub = g.AuthManagementStub(ch)
resp = stub.GetAuthUrl(
    m.GetAuthUrlRequest(provider=m.X_ANDROID, redirect_url="xai-grok://oauth"),
    metadata=GROK_META,
)
auth_url      = resp.auth_url
oauth2_cookie = resp.oauth2_cookie
print(f"  OK — auth_url obtenida.")

# Guardar por si acaso
with open(".oauth_state.json", "w") as f:
    json.dump({"auth_url": auth_url, "oauth2_cookie": oauth2_cookie}, f)

# ── 2. Abrir tab en Chrome y navegar a la URL de Twitter ────────────────────
print("\nAbriendo Chrome con la URL de autorización...")
script_open = f'''
tell application "Google Chrome"
    activate
    set newTab to make new tab at end of tabs of window 1
    set URL of newTab to "{auth_url}"
    return index of newTab
end tell
'''
tab_index, err = run_applescript(script_open)
print(f"  Tab index: {tab_index!r}  err: {err!r}")
time.sleep(4)  # esperar que cargue Twitter

# ── 3. Hacer click en "Authorize app" via JavaScript ────────────────────────
print("Buscando botón Authorize en la página...")
script_auth = f'''
tell application "Google Chrome"
    set tabIdx to {tab_index or 1}
    set theTab to tab tabIdx of window 1
    set jsResult to execute theTab javascript "
        var btns = Array.from(document.querySelectorAll('button, input[type=submit], [role=button]'));
        var authBtn = btns.find(b => b.textContent.match(/Authorize|Allow|Permitir/i));
        if (authBtn) {{
            authBtn.click();
            'clicked: ' + authBtn.textContent.trim();
        }} else {{
            'not found. Buttons: ' + btns.map(b => b.textContent.trim()).join(' | ');
        }}
    "
    return jsResult
end tell
'''
result, err = run_applescript(script_auth)
print(f"  JS result: {result!r}")
time.sleep(5)  # esperar el redirect

# ── 4. Leer la URL actual del tab ───────────────────────────────────────────
print("Leyendo URL del tab después del redirect...")
script_url = f'''
tell application "Google Chrome"
    set tabIdx to {tab_index or 1}
    return URL of tab tabIdx of window 1
end tell
'''
current_url, err = run_applescript(script_url)
print(f"  URL actual: {current_url!r}")

# Si no es xai-grok://, esperar más
if not current_url.startswith("xai-grok://"):
    print("  Esperando redirect (10s más)...")
    time.sleep(10)
    current_url, _ = run_applescript(script_url)
    print(f"  URL actual: {current_url!r}")

# ── 5. También intentar leer el título o el DOM por si capturó algo ─────────
if not current_url.startswith("xai-grok://"):
    script_dom = f'''
    tell application "Google Chrome"
        set tabIdx to {tab_index or 1}
        set theTab to tab tabIdx of window 1
        return execute theTab javascript "window.location.href"
    end tell
    '''
    js_url, _ = run_applescript(script_dom)
    print(f"  window.location.href: {js_url!r}")
    if js_url.startswith("xai-grok://"):
        current_url = js_url

# ── 6. Cerrar tab ────────────────────────────────────────────────────────────
run_applescript(f'tell application "Google Chrome" to close tab {tab_index or 1} of window 1')

# ── 7. Parsear code y state ──────────────────────────────────────────────────
if not current_url.startswith("xai-grok://"):
    print(f"\n❌ No se capturó el redirect. URL final: {current_url}")
    print("   Copiá la URL del tab de Chrome manualmente y ejecutá:")
    print("   python3 step2_finish_login.py 'xai-grok://...'")
    ch.close()
    exit(1)

parsed = urllib.parse.urlparse(current_url)
params = dict(urllib.parse.parse_qsl(parsed.query))
code   = params.get("code", "")
state  = params.get("state", "")
print(f"\ncode : {code[:40]}...")
print(f"state: {state[:40]}...")

# ── 8. CreateSession ─────────────────────────────────────────────────────────
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
        print(f"\n✅ Sesión guardada: {token[:60]}…")
    else:
        print(f"\n⚠️  Sin token. Proto completo: {resp}")
except grpc.RpcError as e:
    print(f"❌ CreateSession: {e.code()} — {e.details()}")

ch.close()
