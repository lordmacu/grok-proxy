#!/usr/bin/env python3
"""Paso 1: obtiene la URL de Twitter y guarda el estado en .oauth_state.json"""
import json, uuid, grpc
import auth_mgmt_pb2 as m
import auth_mgmt_pb2_grpc as g

META = [
    ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
    ("x-app-name",       "Grok Android"),
    ("x-app-version",    "1.2.22"),
    ("x-app-language",   "en-US"),
    ("x-xai-request-id", str(uuid.uuid4())),
]

ch   = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
stub = g.AuthManagementStub(ch)
req  = m.GetAuthUrlRequest(provider=m.X_ANDROID, redirect_url="xai-grok://oauth")
resp = stub.GetAuthUrl(req, metadata=META)

with open(".oauth_state.json", "w") as f:
    json.dump({"auth_url": resp.auth_url, "oauth2_cookie": resp.oauth2_cookie}, f, indent=2)

print(resp.auth_url)
ch.close()
