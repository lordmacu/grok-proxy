#!/usr/bin/env python3
"""
Cliente anónimo para Grok API.
Flujo de auth (una sola vez, se persiste en .anon_creds.json):
  1. Genera keypair secp256k1
  2. CreateAnonUser(compressed_pubkey)  → anon_user_id
  3. CreateAnonUserChallenge(anon_user_id) → challenge bytes
  4. SHA-256(challenge) → secp256k1 compact 64-byte sig
  Credenciales: x-anonuserid / x-challenge / x-signature

Estrategia anti-rate-limit:
  - La cuenta anónima se crea una sola vez y se reutiliza siempre.
  - Si el challenge caduca (o la firma falla), se refresca sin crear cuenta nueva.
  - RESOURCE_EXHAUSTED → backoff exponencial antes de reintentar (nunca spam).
"""
import base64
import hashlib
import json
import os
import sys
import time
import uuid

import coincurve
import grpc

import auth_frontend_pb2 as auth_pb
import auth_frontend_pb2_grpc as auth_grpc
import grok_api_pb2 as chat_pb
import grok_api_pb2_grpc as chat_grpc

HOST = "grok.com"
PORT = 443
CREDS_FILE    = os.path.join(os.path.dirname(__file__), ".anon_creds.json")
SESSION_FILE  = os.path.join(os.path.dirname(__file__), ".session.json")

BASE_METADATA = [
    ("user-agent",    "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",    "Grok Android"),
    ("x-app-version", "1.2.22"),
    ("x-app-language", "en-US"),
]


def _request_id() -> str:
    return str(uuid.uuid4())


def make_channel() -> grpc.Channel:
    return grpc.secure_channel(f"{HOST}:{PORT}", grpc.ssl_channel_credentials())


# ── Crypto ────────────────────────────────────────────────────────────────────

def generate_keypair() -> tuple[bytes, bytes]:
    privkey_bytes = os.urandom(32)
    privkey = coincurve.PrivateKey(privkey_bytes)
    return privkey_bytes, privkey.public_key.format(compressed=True)


def _der_to_compact(der: bytes) -> bytes:
    """DER → compact 64 bytes (r||s), igual que fr.acinq.secp256k1.sign()."""
    assert der[0] == 0x30 and der[2] == 0x02
    r_len = der[3]
    r = der[4:4 + r_len].lstrip(b'\x00')
    rest = der[4 + r_len:]
    assert rest[0] == 0x02
    s = rest[2:2 + rest[1]].lstrip(b'\x00')
    return r.rjust(32, b'\x00') + s.rjust(32, b'\x00')


def sign_challenge(privkey_bytes: bytes, challenge: bytes) -> bytes:
    digest = hashlib.sha256(challenge).digest()
    der = coincurve.PrivateKey(privkey_bytes).sign(digest, hasher=None)
    return _der_to_compact(der)


# ── Auth anónima ──────────────────────────────────────────────────────────────

def _auth_meta() -> list:
    return BASE_METADATA + [("x-xai-request-id", _request_id())]


def _register_new_user(channel: grpc.Channel) -> dict:
    """Crea una cuenta anónima nueva. Llamar solo si no existe una guardada."""
    stub = auth_grpc.AuthFrontendStub(channel)
    privkey_bytes, pubkey = generate_keypair()

    anon_user_id = stub.CreateAnonUser(
        auth_pb.CreateAnonUserRequest(user_public_key=pubkey),
        metadata=_auth_meta(),
    ).anon_user_id
    print(f"[auth] nueva cuenta: {anon_user_id}")

    challenge = stub.CreateAnonUserChallenge(
        auth_pb.CreateChallengeRequest(anon_user_id=anon_user_id),
        metadata=_auth_meta(),
    ).challenge

    signature = sign_challenge(privkey_bytes, challenge)
    return {
        "anon_user_id":  anon_user_id,
        "privkey":       privkey_bytes.hex(),
        "pubkey":        pubkey.hex(),
        "challenge_b64": base64.b64encode(challenge).decode(),
        "signature_b64": base64.b64encode(signature).decode(),
    }


def refresh_challenge(channel: grpc.Channel, creds: dict) -> dict:
    """Renueva challenge + firma reutilizando el mismo anon_user_id y privkey.
    No crea cuenta nueva — solo un nuevo challenge."""
    stub = auth_grpc.AuthFrontendStub(channel)
    privkey_bytes = bytes.fromhex(creds["privkey"])

    challenge = stub.CreateAnonUserChallenge(
        auth_pb.CreateChallengeRequest(anon_user_id=creds["anon_user_id"]),
        metadata=_auth_meta(),
    ).challenge

    signature = sign_challenge(privkey_bytes, challenge)
    creds = dict(creds)
    creds["challenge_b64"] = base64.b64encode(challenge).decode()
    creds["signature_b64"] = base64.b64encode(signature).decode()
    print(f"[auth] challenge refrescado para {creds['anon_user_id']}")
    return creds


def load_or_create_creds(channel: grpc.Channel) -> dict:
    """Carga cuenta del disco; si no existe, crea una nueva (solo una vez)."""
    if os.path.exists(CREDS_FILE):
        with open(CREDS_FILE) as f:
            creds = json.load(f)
        print(f"[auth] reutilizando cuenta: {creds['anon_user_id']}")
        return creds

    creds = _register_new_user(channel)
    with open(CREDS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    return creds


def _save_creds(creds: dict) -> None:
    with open(CREDS_FILE, "w") as f:
        json.dump(creds, f, indent=2)


# ── Chat ──────────────────────────────────────────────────────────────────────

def _auth_headers(creds: dict) -> list:
    return [
        ("x-anonuserid", creds["anon_user_id"]),
        ("x-challenge",  creds["challenge_b64"]),
        ("x-signature",  creds["signature_b64"]),
    ]


# ── Login email/contraseña ────────────────────────────────────────────────────

def login(channel: grpc.Channel, email: str, password: str) -> dict:
    """
    Inicia sesión con email + contraseña.
    Devuelve un dict con session_cookie y lo guarda en SESSION_FILE.
    El token se usa como: Cookie: sso=<token>; sso-rw=<token>
    """
    stub = auth_grpc.AuthFrontendStub(channel)

    creds = auth_pb.CreateSessionRequest.Credentials(
        email_and_password=auth_pb.CreateSessionRequest.EmailAndPassword(
            email=email,
            clear_text_password=password,
        )
    )
    req = auth_pb.CreateSessionRequest(credentials=creds)

    resp = stub.CreateSession(req, metadata=_auth_meta())

    if not resp.session_cookie:
        raise RuntimeError("Login OK pero sin session_cookie en la respuesta")

    session = {
        "email":          email,
        "session_cookie": resp.session_cookie,
        "is_new_user":    resp.is_new_user,
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f, indent=2)

    status = "cuenta nueva" if resp.is_new_user else "sesión iniciada"
    print(f"[login] {status} para {email}")
    return session


def load_session():
    """Carga la sesión guardada, o None si no existe."""
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None


def _session_headers(session: dict) -> list:
    """Headers de auth para cuentas con sesión (email/X/Google)."""
    token = session["session_cookie"]
    return [("cookie", f"sso={token}; sso-rw={token}")]


def chat(channel, message,
         creds=None,
         session=None,
         model: str = "grok-3", reasoning: bool = False,
         max_retries: int = 4) -> tuple:
    """
    Envía un mensaje con streaming token a token.
    Acepta cuenta anónima (creds) o sesión autenticada (session).
    Reintentos con backoff en RESOURCE_EXHAUSTED.
    Devuelve (respuesta_completa, creds_actualizadas_o_None).
    """
    stub = chat_grpc.ChatStub(channel)
    req = chat_pb.CreateConversationAndRespondRequest(
        model_name=model,
        message=message,
        disable_search=False,
        is_reasoning=reasoning,
    )

    def build_meta():
        base = BASE_METADATA + [("x-xai-request-id", _request_id())]
        if session:
            return base + _session_headers(session)
        return base + _auth_headers(creds)

    wait = 5
    for attempt in range(max_retries + 1):
        try:
            print(f"\n[chat] → {message}\n[grok] ", end="", flush=True)
            tokens = []
            for chunk in stub.CreateConversationAndRespond(req, metadata=build_meta()):
                if chunk.HasField("add_response") and chunk.add_response.token:
                    t = chunk.add_response.token
                    print(t, end="", flush=True)
                    tokens.append(t)
            print()
            return "".join(tokens), creds

        except grpc.RpcError as e:
            code = e.code()

            if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                if attempt >= max_retries:
                    raise
                print(f"\n[rate-limit] reintentando en {wait}s "
                      f"(intento {attempt + 1}/{max_retries})…")
                time.sleep(wait)
                wait = min(wait * 2, 120)

            elif code == grpc.StatusCode.UNAUTHENTICATED and creds:
                print("\n[auth] challenge rechazado, refrescando…")
                creds = refresh_challenge(channel, creds)
                _save_creds(creds)

            else:
                raise

    raise RuntimeError("No se pudo completar el chat tras los reintentos")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print("Uso:")
        print("  python3 grok_client.py <pregunta>                    # anónimo")
        print("  python3 grok_client.py --login <email> <contraseña>  # iniciar sesión")
        print("  python3 grok_client.py --session <pregunta>          # usar sesión guardada")
        sys.exit(1)

    with make_channel() as channel:
        if args[0] == "--login":
            if len(args) < 3:
                print("Uso: python3 grok_client.py --login <email> <contraseña>")
                sys.exit(1)
            login(channel, email=args[1], password=args[2])

        elif args[0] == "--session":
            session = load_session()
            if not session:
                print("No hay sesión guardada. Ejecuta primero: --login <email> <contraseña>")
                sys.exit(1)
            question = " ".join(args[1:])
            chat(channel, question, session=session)

        else:
            question = " ".join(args)
            creds = load_or_create_creds(channel)
            _, creds = chat(channel, question, creds=creds)
            _save_creds(creds)


if __name__ == "__main__":
    main()
