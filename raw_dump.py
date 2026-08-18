#!/usr/bin/env python3
"""
Dump de bytes crudos de ResetPasswordByEmailValidationCode.
Intercepta el canal gRPC a bajo nivel para ver qué devuelve el servidor.

Uso: python3 raw_dump.py <CODIGO_OTP>
"""
import sys, uuid, struct
import grpc
from grpc import intercept_channel

EMAIL    = "cgarcialord@gmail.com"
PASSWORD = "Lili-2337677"
HOST     = "grok.com"
PORT     = 443

BASE_META = [
    ("user-agent",    "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",    "Grok Android"),
    ("x-app-version", "1.2.22"),
    ("x-app-language", "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]


class RawDumpInterceptor(grpc.UnaryUnaryClientInterceptor):
    def intercept_unary_unary(self, continuation, client_call_details, request):
        print(f"\n── Request bytes (raw proto, no gRPC framing) ──")
        raw_req = request.SerializeToString()
        _hexdump(raw_req)

        response = continuation(client_call_details, request)
        return response


def _hexdump(data: bytes):
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"  {i:04x}  {hex_part:<47}  {ascii_part}")
    if not data:
        print("  (vacío)")


def encode_grpc_frame(proto_bytes: bytes) -> bytes:
    """Codifica un proto como mensaje gRPC: 1 byte compresión + 4 bytes longitud."""
    return b'\x00' + struct.pack('>I', len(proto_bytes)) + proto_bytes


def decode_grpc_response(data: bytes) -> bytes:
    """Extrae el proto del frame gRPC (sin compresión)."""
    if len(data) < 5:
        return b''
    compressed = data[0]
    length     = struct.unpack('>I', data[1:5])[0]
    return data[5:5+length]


def main():
    if len(sys.argv) != 2:
        print("Uso: python3 raw_dump.py <CODIGO_OTP>")
        sys.exit(1)

    code = sys.argv[1]

    # Construir el request proto manualmente usando protobuf
    import auth_mgmt_pb2 as mgmt_pb
    import auth_mgmt_pb2_grpc as mgmt_grpc

    req = mgmt_pb.ResetPasswordByEmailValidationCodeRequest(
        email_validation_code=code,
        clear_text_password=PASSWORD,
        email=EMAIL,
        num_one_time_links=0,
    )
    print(f"── Request proto (campos) ──")
    print(req)

    creds   = grpc.ssl_channel_credentials()
    channel = grpc.secure_channel(f"{HOST}:{PORT}", creds)

    stub = mgmt_grpc.AuthManagementStub(channel)

    # Llamada con metadatos de respuesta completa
    try:
        call, resp = stub.ResetPasswordByEmailValidationCode.with_call(
            req, metadata=BASE_META
        )
        print(f"\n── Response ──")
        print(repr(resp))

        print(f"\n── Initial metadata ──")
        try:
            for k, v in stub.ResetPasswordByEmailValidationCode.initial_metadata():
                print(f"  {k}: {v!r}")
        except:
            pass

        print(f"\n── Trailing metadata ──")
        try:
            for k, v in call.trailing_metadata():
                print(f"  {k}: {v!r}")
        except Exception as e:
            print(f"  (error: {e})")

    except grpc.RpcError as e:
        print(f"\n── RPC Error ──")
        print(f"  code   : {e.code()}")
        print(f"  details: {e.details()}")
        try:
            for k, v in e.trailing_metadata():
                print(f"  trailer: {k}={v!r}")
        except:
            pass

    channel.close()


if __name__ == "__main__":
    main()
