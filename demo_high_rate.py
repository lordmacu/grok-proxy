#!/usr/bin/env python3
"""
Demo de los modelos con 999 requests/hora.

Correr:
  python3 demo_high_rate.py              # menú interactivo
  python3 demo_high_rate.py "tu pregunta"  # pregunta directa

Modelos disponibles (todos 999/hora, todos Grok 4.5):
  imagine-agent-mode          → imagen gen (más limpio para texto)
  grok-plugins-4p6-excel      → Excel plugin
  grok-plugins-4p6-docs       → Google Docs plugin
  grok-plugins-4p6-sheets     → Google Sheets plugin
  grok-plugins-4p6-word       → Word plugin
  grok-plugins-4p6-powerpoint → PowerPoint plugin
  grok-plugins-4p6-outlook    → Outlook plugin
  grok-plugins-4p6-slides     → Slides plugin
"""
import grpc, uuid, json, sys, os

import grok_api_pb2 as ga
import grok_api_pb2_grpc as gg

SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session.json")
MODEL        = "imagine-agent-mode"   # cambiar si querés probar otro


def load_session():
    with open(SESSION_FILE) as f:
        return json.load(f)["session_cookie"]


def make_meta(token):
    return [
        ("user-agent",       "Grok/1.2.22 (Android; arm64-v8a)"),
        ("x-app-name",       "Grok Android"),
        ("x-app-version",    "1.2.22"),
        ("x-app-language",   "en-US"),
        ("x-xai-request-id", str(uuid.uuid4())),
        ("cookie",           f"sso={token}; sso-rw={token}"),
    ]


def chat(message, model=MODEL, token=None):
    if token is None:
        token = load_session()
    ch = grpc.secure_channel("grok.com:443", grpc.ssl_channel_credentials())
    stub = gg.ChatStub(ch)
    req = ga.CreateConversationAndRespondRequest(
        model_name=model,
        message=message,
        disable_search=True,
    )
    print(f"\n[modelo: {model}]\n")
    result = []
    for chunk in stub.CreateConversationAndRespond(req, metadata=make_meta(token), timeout=120):
        if chunk.HasField("add_response") and chunk.add_response.token:
            t = chunk.add_response.token
            # filtrar artefactos de tool calls
            if "<xai:" in t:
                continue
            print(t, end="", flush=True)
            result.append(t)
    print("\n")
    ch.close()
    return "".join(result)


EJEMPLOS = [
    ("Código Python",    "Write a quicksort implementation in Python with type hints and explain the time complexity."),
    ("Razonamiento",     "A snail is at the bottom of a 10-meter well. Each day it climbs 3 meters, each night it slides back 2 meters. How many days to escape?"),
    ("Matemática",       "Explain why 0.999... equals exactly 1, using at least two different proofs."),
    ("Texto libre",      "Write a 3-sentence noir detective story opening set in Buenos Aires."),
    ("Código SQL",       "Write a SQL query to find the top 3 customers by total spend in the last 30 days, with ties handled."),
]


def main():
    token = load_session()

    if len(sys.argv) > 1:
        # Pregunta directa desde CLI
        chat(" ".join(sys.argv[1:]), token=token)
        return

    # Menú interactivo
    print("=== Demo modelos 999/hora ===\n")
    print("Elegí un ejemplo o escribí tu propia pregunta:\n")
    for i, (titulo, _) in enumerate(EJEMPLOS, 1):
        print(f"  {i}. {titulo}")
    print("  0. Escribir pregunta propia")
    print()

    opcion = input("Opción: ").strip()
    if opcion == "0":
        pregunta = input("Tu pregunta: ").strip()
    elif opcion.isdigit() and 1 <= int(opcion) <= len(EJEMPLOS):
        _, pregunta = EJEMPLOS[int(opcion) - 1]
    else:
        print("Opción inválida")
        return

    chat(pregunta, token=token)


if __name__ == "__main__":
    main()
