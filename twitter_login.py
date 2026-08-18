#!/usr/bin/env python3
"""
Login vía Twitter/X OAuth para cuenta de Grok creada con Twitter.

Cómo funciona:
  1. GetAuthUrl → URL de Twitter para autorizar
  2. Servidor local en :8888 captura el callback con code y state
  3. CreateSession(oauth=OAuth2Response(code, state, oauth2_cookie)) → sesión

Uso: python3 twitter_login.py
"""
import json, uuid, threading, urllib.parse, webbrowser, grpc
from http.server import HTTPServer, BaseHTTPRequestHandler

import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

SESSION_FILE = ".session.json"
PORT = 8888

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

callback_params = {}
server_instance = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        callback_params.update(params)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>Autorizado correctamente.</h2>"
            b"<p>Podes cerrar esta ventana y volver a la terminal.</p>"
        )
        threading.Thread(target=server_instance.shutdown, daemon=True).start()

    def log_message(self, *_):
        pass


def get_auth_url(ch, redirect_url):
    stub = g.AuthManagementStub(ch)
    req = m.GetAuthUrlRequest(
        provider=m.X_ANDROID,
        redirect_url=redirect_url,
    )
    try:
        resp = stub.GetAuthUrl(req, metadata=META)
        return resp
    except grpc.RpcError as e:
        print(f"❌ GetAuthUrl: {e.code()} — {e.details()}")
        return None


def create_session(ch, code, state, oauth2_cookie):
    stub = g.AuthManagementStub(ch)

    meta = list(META)
    if oauth2_cookie:
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
        return resp
    except grpc.RpcError as e:
        print(f"❌ CreateSession: {e.code()} — {e.details()}")
        return None


def main():
    global server_instance

    ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())

    # Intentar con localhost primero, si falla usar xai-grok://oauth
    for redirect_url in [f"http://localhost:{PORT}/callback", "xai-grok://oauth"]:
        print(f"Probando redirect_url={redirect_url!r} ...")
        auth_resp = get_auth_url(ch, redirect_url)
        if auth_resp and auth_resp.auth_url:
            print(f"✅ auth_url obtenida.")
            break
    else:
        print("No se pudo obtener auth_url.")
        ch.close()
        return

    auth_url     = auth_resp.auth_url
    oauth2_cookie = auth_resp.oauth2_cookie

    print(f"\noauth2_cookie del servidor: {oauth2_cookie!r}")

    if "localhost" in redirect_url:
        # Levantar servidor local y abrir browser automáticamente
        server_instance = HTTPServer(("localhost", PORT), CallbackHandler)
        print(f"\nAbriendo browser para autorizar con Twitter...")
        print(f"URL: {auth_url}\n")
        webbrowser.open(auth_url)
        print(f"Esperando callback en http://localhost:{PORT} ...")
        server_instance.serve_forever()
    else:
        # xai-grok:// → el user copia la URL de redirección manualmente
        print(f"\nAbrí esta URL en el browser y logueate con Twitter:")
        print(f"  {auth_url}")
        print(f"\nDespués de autorizar, Twitter va a redirigir a una URL que empieza con")
        print(f"'xai-grok://oauth?...' — copiá esa URL completa y pegala acá:")
        raw_url = input("> ").strip()
        parsed  = urllib.parse.urlparse(raw_url)
        callback_params.update(dict(urllib.parse.parse_qsl(parsed.query)))

    code  = callback_params.get("code", "")
    state = callback_params.get("state", "")

    if not code:
        # Tal vez Twitter devuelve oauth_token/oauth_verifier (OAuth1)
        code  = callback_params.get("oauth_token", "")
        state = callback_params.get("oauth_verifier", "")

    print(f"\ncode  : {code!r}")
    print(f"state : {state!r}")
    print(f"todos los params: {callback_params}")

    if not code:
        print("No se encontró 'code' en el callback. Saliendo.")
        ch.close()
        return

    print("\nCreando sesión en Grok...")
    resp = create_session(ch, code, state, oauth2_cookie)

    if resp:
        raw = resp.SerializeToString()
        print(f"Response bytes ({len(raw)}): {raw.hex() or '(vacío)'}")
        sr = resp.session_response if hasattr(resp, 'session_response') else resp
        cookie = getattr(sr, 'session_cookie', '') or ''
        sid    = getattr(getattr(sr, 'session', None), 'session_id', '') or ''
        token  = cookie or sid
        if token:
            with open(SESSION_FILE, "w") as f:
                json.dump({"session_cookie": token}, f, indent=2)
            print(f"✅ Sesión guardada: {token[:40]}…")
        else:
            print("⚠️  Sin token en la respuesta.")

    ch.close()


if __name__ == "__main__":
    main()
