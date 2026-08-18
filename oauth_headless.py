#!/usr/bin/env python3
"""
Login headless: extrae cookies de Twitter de Chrome, sigue el flujo OAuth2
de xAI sin abrir ningún browser, captura el code y crea la sesión de Grok.
"""
import json, uuid, urllib.parse, grpc, httpx, browser_cookie3

import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

GROK_META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

# ── 1. Obtener URL fresca de GetAuthUrl ──────────────────────────────────────
print("Obteniendo auth_url de Grok...")
ch   = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
stub = g.AuthManagementStub(ch)
resp = stub.GetAuthUrl(
    m.GetAuthUrlRequest(provider=m.X_ANDROID, redirect_url="xai-grok://oauth"),
    metadata=GROK_META,
)
auth_url     = resp.auth_url
oauth2_cookie = resp.oauth2_cookie
print(f"  auth_url     : {auth_url[:80]}...")
print(f"  oauth2_cookie: {oauth2_cookie[:40]}...")

# ── 2. Extraer cookies de Twitter/X de Chrome ────────────────────────────────
print("\nExtrayendo cookies de Chrome para x.com...")
cookie_jar = browser_cookie3.chrome(domain_name=".x.com")
cookies = {c.name: c.value for c in cookie_jar}
print(f"  Cookies encontradas: {list(cookies.keys())}")

if not cookies.get("auth_token"):
    print("❌ No se encontró 'auth_token' — ¿estás logueado en x.com en Chrome?")
    ch.close()
    exit(1)

# ── 3. Seguir el flujo OAuth en Twitter con httpx ───────────────────────────
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

captured_code  = None
captured_state = None

def on_redirect(resp):
    global captured_code, captured_state
    loc = resp.headers.get("location", "")
    print(f"  → redirect: {loc[:100]}")
    if loc.startswith("xai-grok://"):
        parsed = urllib.parse.urlparse(loc)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        captured_code  = params.get("code", "")
        captured_state = params.get("state", "")
        raise httpx.LocalProtocolError("captured")  # cortar la cadena de redirects

client = httpx.Client(
    cookies=cookies,
    headers=headers,
    follow_redirects=False,
    timeout=30,
)

print(f"\nSiguiendo flujo OAuth en x.com...")
url = auth_url
for i in range(15):
    try:
        r = client.get(url)
    except Exception as e:
        print(f"  error: {e}")
        break

    loc = r.headers.get("location", "")
    print(f"  [{r.status_code}] {url[:80]}")

    if r.status_code in (301, 302, 303, 307, 308) and loc:
        if loc.startswith("xai-grok://"):
            parsed = urllib.parse.urlparse(loc)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            captured_code  = params.get("code", "")
            captured_state = params.get("state", "")
            print(f"  ✅ code capturado!")
            break
        # resolver relativas
        if loc.startswith("/"):
            base = urllib.parse.urlparse(url)
            loc  = f"{base.scheme}://{base.netloc}{loc}"
        url = loc
        continue

    # Respuesta final: puede ser la página de autorización
    if r.status_code == 200:
        body = r.text
        if "Authorize app" in body or "authorize" in body.lower():
            print("  Página de autorización detectada — buscando endpoint de confirm...")
            # Buscar el endpoint de autorización en el HTML
            # Twitter usa un formulario POST o una petición fetch a /i/api/2/oauth2/authorize
            if "authenticity_token" in body:
                import re
                token_m = re.search(r'name="authenticity_token" value="([^"]+)"', body)
                auth_tk = token_m.group(1) if token_m else ""
                # POST al formulario
                post_data = {
                    "authenticity_token": auth_tk,
                    "redirect_after_login": url,
                }
                print(f"  POST con authenticity_token={auth_tk[:20]}...")
                r2 = client.post(url, data=post_data)
                loc2 = r2.headers.get("location", "")
                print(f"  [{r2.status_code}] location={loc2[:100]}")
                if loc2.startswith("xai-grok://"):
                    parsed = urllib.parse.urlparse(loc2)
                    params = dict(urllib.parse.parse_qsl(parsed.query))
                    captured_code  = params.get("code", "")
                    captured_state = params.get("state", "")
                    break
            else:
                # Intentar API directa de Twitter OAuth2
                # Extraer client_id, state, code_challenge del auth_url original
                orig = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
                api_url = "https://x.com/i/api/2/oauth2/authorize"
                r2 = client.post(api_url, json={
                    "approval": "true",
                    "code_challenge": orig.get("code_challenge", ""),
                    "code_challenge_method": "S256",
                    "client_id": orig.get("client_id", ""),
                    "redirect_uri": "xai-grok://oauth",
                    "response_type": "code",
                    "scope": orig.get("scope", ""),
                    "state": orig.get("state", ""),
                }, headers={**headers, "Content-Type": "application/json", "X-Twitter-Auth-Type": "OAuth2Session"})
                print(f"  API [{r2.status_code}]: {r2.text[:200]}")
                try:
                    data = r2.json()
                    redirect = data.get("redirect_uri", "")
                    if redirect.startswith("xai-grok://"):
                        parsed = urllib.parse.urlparse(redirect)
                        params = dict(urllib.parse.parse_qsl(parsed.query))
                        captured_code  = params.get("code", "")
                        captured_state = params.get("state", "")
                except Exception:
                    pass
        break

client.close()

if not captured_code:
    print("\n❌ No se capturó el code OAuth2.")
    ch.close()
    exit(1)

print(f"\ncode : {captured_code[:40]}...")
print(f"state: {captured_state[:40]}...")

# ── 4. CreateSession ─────────────────────────────────────────────────────────
print("\nCreando sesión en Grok...")
meta = list(GROK_META)
meta.append(("cookie", oauth2_cookie))

req = m.CreateSessionRequest(
    credentials=m.CreateSessionRequest.Credentials(
        oauth=m.CreateSessionRequest.OAuth2Response(
            code=captured_code,
            state=captured_state,
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
        print(f"\n⚠️  Sin token. Proto: {resp}")
except grpc.RpcError as e:
    print(f"❌ CreateSession: {e.code()} — {e.details()}")

ch.close()
