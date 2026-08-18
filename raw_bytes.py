#!/usr/bin/env python3
"""
Hace el call de OTP reset y vuelca TODO: bytes proto + toda la metadata HTTP.
Uso: python3 raw_bytes.py <CODIGO_OTP>
"""
import sys, uuid, json, grpc
import auth_mgmt_pb2 as mgmt_pb
import auth_mgmt_pb2_grpc as mgmt_grpc

EMAIL    = "cgarcialord@gmail.com"
PASSWORD = "Lili-2337677"

def main():
    code = sys.argv[1] if len(sys.argv) > 1 else input("OTP: ").strip()
    meta_send = [
        ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
        ("x-app-name",       "Grok Android"),
        ("x-app-version",    "1.2.22"),
        ("x-app-language",   "en-US"),
        ("x-xai-request-id", str(uuid.uuid4())),
    ]
    ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
    stub = mgmt_grpc.AuthManagementStub(ch)

    req = mgmt_pb.ResetPasswordByEmailValidationCodeRequest(
        email_validation_code=code,
        clear_text_password=PASSWORD,
        email=EMAIL,
        num_one_time_links=1,
    )
    try:
        resp, call = stub.ResetPasswordByEmailValidationCode.with_call(req, metadata=meta_send)

        raw = resp.SerializeToString()
        print(f"proto bytes ({len(raw)}): {raw.hex() or '(vacío)'}")

        print("\n── initial metadata (HTTP response headers) ──")
        for k, v in call.initial_metadata():
            print(f"  {k}: {v!r}")

        print("\n── trailing metadata (HTTP trailers) ──")
        for k, v in call.trailing_metadata():
            print(f"  {k}: {v!r}")

        print("\n── campos proto ──")
        sr = resp.session_response
        print(f"  session_cookie      : {sr.session_cookie!r}")
        print(f"  session.session_id  : {sr.session.session_id!r}")
        print(f"  session.user_id     : {sr.session.user_id!r}")
        print(f"  is_new_user         : {sr.is_new_user}")
        print(f"  one_time_link_tokens: {list(sr.one_time_link_tokens)}")

        # Guardar sesión si encontramos algo útil
        token = sr.session_cookie or sr.session.session_id
        links = list(sr.one_time_link_tokens)
        if token:
            with open(".session.json", "w") as f:
                json.dump({"email": EMAIL, "session_cookie": token,
                           "user_id": sr.session.user_id}, f, indent=2)
            print(f"\n✅ Sesión guardada (token={token[:30]}…)")
        elif links:
            print(f"\n⚠️  one_time_link_token: {links[0]}")
            with open(".session.json", "w") as f:
                json.dump({"email": EMAIL, "one_time_link_token": links[0]}, f, indent=2)
        else:
            print("\n⚠️  Sin token en proto ni en metadata")

    except grpc.RpcError as e:
        print(f"error: {e.code()} — {e.details()}")
        try:
            for k, v in e.initial_metadata():
                print(f"  init: {k}={v!r}")
            for k, v in e.trailing_metadata():
                print(f"  trail: {k}={v!r}")
        except:
            pass
    ch.close()

if __name__ == "__main__":
    main()
