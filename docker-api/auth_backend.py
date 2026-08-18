"""
Flujos de autenticación contra Grok:
  - Email + contraseña  → POST /auth/login
  - OTP (cuentas sin password, ej. Twitter/Google)
      → POST /auth/otp/send    (manda el código al email)
      → POST /auth/otp/verify  (verifica y devuelve el token)
"""
import uuid
import grpc

import auth_mgmt_pb2 as am
import auth_mgmt_pb2_grpc as amg

HOST = "grok.com:443"

BASE_META = [
    ("user-agent",    "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",    "Grok Android"),
    ("x-app-version", "1.2.22"),
    ("x-app-language", "en-US"),
]


def _meta():
    return BASE_META + [("x-xai-request-id", str(uuid.uuid4()))]


def _channel():
    return grpc.secure_channel(HOST, grpc.ssl_channel_credentials())


# ── Email + contraseña ────────────────────────────────────────────────────────

def login_email_password(email: str, password: str) -> str:
    """
    Login directo con email y contraseña.
    Retorna el session_cookie.
    Lanza RuntimeError si falla.
    """
    with _channel() as ch:
        stub = amg.AuthManagementStub(ch)
        req = am.CreateSessionRequest(
            credentials=am.CreateSessionRequest.Credentials(
                email_and_password=am.CreateSessionRequest.EmailAndPasswordRequest(
                    email=email,
                    clear_text_password=password,
                )
            )
        )
        try:
            resp = stub.CreateSession(req, metadata=_meta())
        except grpc.RpcError as e:
            raise RuntimeError(f"Login fallido: {e.details()}") from e

        if not resp.session_cookie:
            raise RuntimeError("Login OK pero sin session_cookie en la respuesta")

        return resp.session_cookie


# ── OTP (para cuentas sin contraseña: Twitter/Google OAuth) ──────────────────

def otp_send(email: str) -> None:
    """
    Envía un código OTP al email para el flujo RESET_PASSWORD.
    Usa este flujo para cuentas creadas vía Twitter/Google que no tienen password.
    """
    with _channel() as ch:
        stub = amg.AuthManagementStub(ch)
        req = am.CreateEmailValidationCodeRequest(
            email=email,
            email_template=am.RESET_PASSWORD,
        )
        try:
            stub.CreateEmailValidationCode(req, metadata=_meta())
        except grpc.RpcError as e:
            raise RuntimeError(f"No se pudo enviar el OTP: {e.details()}") from e


def otp_verify(email: str, code: str) -> str:
    """
    Verifica el OTP y retorna el session_cookie.
    El código puede venir con o sin guion (ej: '938-612' o '938612').
    num_one_time_links=1 es necesario para que el servidor devuelva la sesión
    en cuentas Twitter/Google (sin esto retorna 0 bytes).
    """
    code_clean = code.replace("-", "").strip()

    with _channel() as ch:
        stub = amg.AuthManagementStub(ch)
        req = am.ResetPasswordByEmailValidationCodeRequest(
            email_validation_code=code_clean,
            clear_text_password="",   # no se usa pero el campo existe
            email=email,
            num_one_time_links=1,     # clave: sin esto → respuesta vacía
        )
        try:
            resp = stub.ResetPasswordByEmailValidationCode(req, metadata=_meta())
        except grpc.RpcError as e:
            raise RuntimeError(f"OTP inválido: {e.details()}") from e

        token = resp.session_response.session_cookie
        if not token:
            raise RuntimeError("OTP verificado pero sin session_cookie — ¿código ya usado?")

        return token
