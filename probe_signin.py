#!/usr/bin/env python3
"""
Prueba métodos de login para cgarcialord@gmail.com.
Sin castle token — observamos el error para saber si la cuenta tiene email/pass o es Google-only.
"""
import uuid, grpc
import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

EMAIL    = "cgarcialord@gmail.com"
PASSWORD = "Lili-2337677"

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

def call(stub_fn, req, label):
    try:
        resp = stub_fn(req, metadata=META)
        raw = resp.SerializeToString()
        print(f"✅ {label}")
        print(f"   bytes({len(raw)}): {raw.hex() or '(vacío)'}")
        print(f"   repr: {resp}")
        # Si es CreateSessionV2Response con ExistingEmailSignInMethods
        if hasattr(resp, 'existing_email_sign_in_methods'):
            eem = resp.existing_email_sign_in_methods
            if eem.sign_in_methods:
                print(f"   sign_in_methods: {list(eem.sign_in_methods)}")
                print(f"   oauth_data_token: {eem.oauth_data_token!r}")
    except grpc.RpcError as e:
        print(f"❌ {label}")
        print(f"   {e.code()} — {e.details()}")


ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
stub = g.AuthManagementStub(ch)

creds = m.CreateSessionRequest.Credentials(
    email_and_password=m.CreateSessionRequest.EmailAndPasswordRequest(
        email=EMAIL,
        clear_text_password=PASSWORD,
    )
)
req_base = m.CreateSessionRequest(credentials=creds)

call(stub.CreateSession,   req_base, "CreateSession (sin castle)")
call(stub.CreateSessionV2, req_base, "CreateSessionV2 (sin castle)")
call(stub.CreateSessionV2,
     m.CreateSessionRequest(credentials=creds, prompt_on_duplicate_email=True),
     "CreateSessionV2 (prompt_on_duplicate_email=True)")

ch.close()
