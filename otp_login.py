#!/usr/bin/env python3
"""
Login vía OTP: pide código al email y extrae la sesión.

Uso:
  python3 otp_login.py request             # solicita un OTP nuevo
  python3 otp_login.py verify <CODE>       # envía el OTP y guarda la sesión
"""
import json, os, sys, uuid

import grpc
import auth_mgmt_pb2 as mgmt_pb
import auth_mgmt_pb2_grpc as mgmt_grpc

HOST = "grok.com"
PORT = 443
SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session.json")
EMAIL = "cgarcialord@gmail.com"
PASSWORD = "Lili-2337677"

BASE_METADATA = [
    ("user-agent",    "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",    "Grok Android"),
    ("x-app-version", "1.2.22"),
    ("x-app-language", "en-US"),
]

def meta():
    return BASE_METADATA + [("x-xai-request-id", str(uuid.uuid4()))]

def make_channel():
    return grpc.secure_channel(f"{HOST}:{PORT}", grpc.ssl_channel_credentials())

def request_otp(channel):
    stub = mgmt_grpc.AuthManagementStub(channel)
    req = mgmt_pb.CreateEmailValidationCodeRequest(
        email=EMAIL,
        email_template=mgmt_pb.RESET_PASSWORD,
    )
    stub.CreateEmailValidationCode(req, metadata=meta())
    print(f"OTP enviado a {EMAIL}. Ejecuta:")
    print(f"  python3 otp_login.py verify <CODIGO>")

def verify_otp(channel, code):
    stub = mgmt_grpc.AuthManagementStub(channel)
    req = mgmt_pb.ResetPasswordByEmailValidationCodeRequest(
        email_validation_code=code,
        clear_text_password=PASSWORD,
        email=EMAIL,
        num_one_time_links=0,
    )

    # Llamada con acceso a metadata de respuesta
    call = stub.ResetPasswordByEmailValidationCode.future(req, metadata=meta())
    resp = call.result()

    print("\n── Respuesta raw ──")
    print(resp)

    sr = resp.session_response
    print(f"\nis_new_user       : {sr.is_new_user}")
    print(f"session_cookie    : {sr.session_cookie!r}")
    print(f"one_time_link_tok : {list(sr.one_time_link_tokens)}")
    print(f"session.session_id: {sr.session.session_id!r}")
    print(f"session.user_id   : {sr.session.user_id!r}")

    # Intentar obtener trailing metadata (por si el token viene en headers HTTP)
    try:
        trailing = call.trailing_metadata()
        print(f"\nTrailing metadata:")
        for k, v in trailing:
            print(f"  {k}: {v!r}")
    except Exception as e:
        print(f"\n(trailing metadata: {e})")

    # Determinar cuál es el token SSO
    token = sr.session_cookie or sr.session.session_id
    if not token:
        print("\n⚠️  No se encontró token en session_cookie ni session.session_id")
        return

    session = {
        "email":          EMAIL,
        "session_cookie": token,
        "is_new_user":    sr.is_new_user,
        "user_id":        sr.session.user_id,
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f, indent=2)
    print(f"\n✅ Sesión guardada en {SESSION_FILE}")
    print(f"   token: {token[:30]}…")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    with make_channel() as ch:
        if args[0] == "request":
            request_otp(ch)
        elif args[0] == "verify" and len(args) == 2:
            verify_otp(ch, args[1])
        else:
            print(__doc__)
            sys.exit(1)

if __name__ == "__main__":
    main()
