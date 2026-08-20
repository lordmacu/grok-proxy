# Capacidades de grok-proxy

A diferencia de chatgpt-proxy, grok no tiene planes ni cuentas escalonadas:
cada RPC viaja sobre un único token de sesión (`GROK_SESSION_TOKEN`). Por
eso no hay una matriz anónima/free/plan que comparar — solo hay dos
estados, **con sesión** y **sin sesión** — y las once capacidades del
contrato son, en la práctica, "¿esta RPC concreta funciona con el token que
este despliegue tiene configurado ahora mismo?".

`capabilities.snapshot()` (en `docker-api/capabilities.py`) resuelve ese
estado leyendo la variable de entorno una sola vez, sin red y sin caché.
`capabilities.effective()` traduce el estado a los once booleanos. `GET
/health` los publica bajo `capabilities`; es la fuente que lee la máquina
(el gateway llm-libre), y no se desactualiza porque no hay nada que
recordar sincronizar: se recalcula en cada request.

## Matriz de capacidades

| Capacidad | Valor | RPC | Verificación |
|---|:---:|---|---|
| `chat` | ✅ (con sesión) | `Chat/AddResponse` vía `POST /v1/chat/completions` | Uso diario del proxy; es el camino principal. |
| `streaming` | ✅ (con sesión) | Igual, con `stream: true` → SSE token a token | Uso diario del proxy. |
| `tools` | ✅ (con sesión) | `Chat/AddResponse`, `tool_calls` nativos en la respuesta | Medido en vivo contra el pool de modelos; la familia `imagine-agent-mode` queda excluida (son generadores de imagen, no chat). |
| `vision` | ✅ (con sesión) | `image_url` subido y adjuntado al turno de `Chat/AddResponse` | 30/31 rutas del pool leyeron correctamente un código de 4 dígitos en una imagen de prueba. |
| `images` | ✅ (con sesión) | `Media/GenerateImage` vía la familia `imagine-agent-mode` | Único grupo de modelos grok que genera imágenes; probado en vivo. |
| `audio_speech` | ✅ (con sesión) | `Chat/TextToSpeech` (streaming, concatenado) vía `POST /v1/audio/speech` | Devuelve bytes de audio crudos con el `Content-Type` correcto, no un sobre JSON. La voz por defecto sale de `Voice/ListTopVoices`, no de un id fijo que podría no existir en la cuenta. |
| `audio_transcription` | ✅ (con sesión) | `Voice/SpeechToText` (unaria) vía `POST /v1/audio/transcriptions` | Devuelve `{"text": ...}` o texto plano con `response_format: "text"`. Ver nota sobre `Voice/Transcribe` abajo. |
| `translate` | ❌ (siempre) | — | Grok no tiene endpoint de traducción. Enrutarla a través de un turno de chat sería otra capacidad disfrazada de esta — no es lo mismo medir "el modelo puede traducir si se lo pido en el prompt" que "existe una superficie de traducción verificada", así que se declara `false` sin condiciones. |
| `search` | ✅ (con sesión) | `Chat/AddResponse` con `disable_search` invertido, vía `web_search` en `POST /v1/chat/completions` (y en los endpoints de mensajes de conversación) | El campo nativo de grok es `disable_search`; este proxy lo invierte y lo deja **encendido por defecto**, igual que el resto de proveedores. Esto es un cambio de comportamiento respecto de versiones anteriores de este proxy, que no tocaban ese campo. Ver `main.resolve_disable_search`. |
| `files` | ❌ (siempre) | `Chat/UploadFile` vía `POST /grok/files` (no `/v1/files`) | Ver sección dedicada abajo — el booleano cubre la superficie completa en el path estándar, y ese path responde `501` en los cuatro métodos. |
| `conversations` | ✅ (con sesión) | `alias de backend.list_conversations` / `backend.get_conversation` vía `GET /v1/conversations` y `GET /v1/conversations/{id}` | Envuelve la lista como `{"object": "list", "data": [...]}` y mapea `conversation_id` → `id`. La superficie nativa `/grok/conversations` (rename, delete, share, messages) sigue intacta y es más rica. |

Sin sesión (`GROK_SESSION_TOKEN` vacío o no configurado) las nueve
capacidades marcadas "✅ (con sesión)" caen a `false`: no hay ningún RPC de
grok que no viaje sobre ese token. `translate` y `files` quedan en `false`
sin importar el estado de la sesión.

## Por qué `translate` es `false`

Grok no expone ningún endpoint de traducción en el gRPC decompilado.
Simular una traducción pidiéndoselo al modelo dentro de un turno de chat
normal produciría *algo*, pero sería una capacidad distinta — "el chat
puede seguir instrucciones de traducción" — vistiéndose con el nombre de
esta. El contrato exige que el booleano describa lo que la RPC dedicada
logra, no lo que un prompt astuto podría lograr, así que `translate` se
queda en `false` de forma permanente.

## Por qué `files` es `false` aunque subir funciona

Subir un archivo funciona hoy, de verdad: llama a
`grok_api.Chat/UploadFile`, una RPC real y verificada. Pero **no vive en
`/v1/files`** — vive en `POST /grok/files`, la superficie nativa de este
proxy. Los cuatro métodos de `/v1/files*` (`POST`, `GET`, `GET /{id}`,
`DELETE /{id}`) responden `501`, la creación incluida.

Esto es intencional, no un déficit temporal: el booleano `files` del
contrato promete la **superficie completa** — crear, listar, obtener y
borrar — servida en el path estándar de OpenAI, y grok solo puede
honrar una parte de eso (la creación) mientras el resto (listar, obtener,
borrar) siga sin verificar. Dejar la creación funcionando en `/v1/files`
mientras el booleano dice `false` serviría solo a un cliente que ignora el
contrato — uno que confía en `files: false` nunca llamaría ahí, así que no
pierde nada; uno que no lo lee obtendría una superficie a medias vestida
de superficie completa, que es exactamente lo que el contrato existe para
evitar. Por eso `/v1/files` responde `501` en los cuatro métodos y la
creación real vive, sin ambigüedad, bajo el prefijo nativo `/grok/files`
— ver §3.4 de la spec: *"anything a provider offers beyond this lives
under its own prefix."*

El registro de archivos a nivel de cuenta, si existe, parece vivir en un
namespace distinto: `grok_api_v2.AssetRepository`
(`ListAssetMetadata`/`GetAssetMetadata`/`DeleteAsset`, indexado por
`asset_id`, con campos — `mime_type`, `name`, `size_bytes`, `create_time`
— que mapean limpio a un objeto `file` de OpenAI). `Chat/UploadFile`
devuelve, en cambio, un `file_metadata_id`. Si un archivo subido por chat
aparece luego como un asset en `AssetRepository` **no se ha medido contra
una cuenta real** — eso es lo que dejaría `GET`/`DELETE` con una RPC real
que apuntar, no lo que decide si la creación vive en `/v1/files` o en
`/grok/files`.

**La prueba en vivo que lo resolvería**: subir un archivo pequeño por
`/grok/files`, anotar el `file_id` devuelto, y llamar a
`grok_api_v2.AssetRepository/ListAssetMetadata` para ver si ese id (o uno
relacionado) aparece como `asset_id` en la respuesta. Si aparece, `files`
puede pasar a `true` y los handlers de listar/obtener/borrar pueden
apuntar a la RPC real; si no aparece, el booleano se queda en `false` con
más certeza todavía.

`grok_api_v2.FilesService/ListFiles` existe y es real, pero es un
filesystem **con alcance de conversación**, no el registro de cuenta que
un `GET /v1/files` sin filtros de OpenAI espera — por eso está servido
aparte, en `GET /grok/conversations/{conv_id}/files`, y no cuenta para
este booleano.

## `Voice/Transcribe` frente a `Voice/SpeechToText`

Grok tiene dos RPC de voz a texto. Este proxy usa `Voice/SpeechToText`
(unaria, con un campo `text` de nivel superior que mapea directo al `{"text":
...}` que promete `audio_transcription`). La alternativa,
`Voice/Transcribe`, devuelve `TranscribeResponse { 1: segments }` — una
lista de segmentos que habría que coser en una sola transcripción antes de
poder devolverla. No se usa aquí a propósito: añadiría una etapa de
ensamblado que `SpeechToText` no necesita, para el mismo resultado final.

## `search` ahora está encendido por defecto

El campo nativo de grok es `disable_search` (apagar es la acción
explícita). Este proxy lo invierte para exponer `web_search`, con
búsqueda **encendida por defecto** — igual que el resto de proveedores que
llm-libre integra. Esto es un cambio de comportamiento respecto de
versiones anteriores de grok-proxy, que no tocaban ese campo y dejaban a
grok en su default nativo. Ver `main.resolve_disable_search` y
`tests/test_web_search.py`.

## El gate `501`

Un endpoint cuya capacidad está en `false` responde **`501`**, nunca `404`
ni `503`: un `404` es indistinguible de un error de ruteo, y un `503` hace
que el gateway reintente y acumule sospecha contra una ruta que en esta
configuración nunca iba a funcionar. `501` dice: este proxy, a propósito,
no hace esto ahora mismo.

`main.require_capability(name)` implementa el gate: es una función
**síncrona** — `capabilities.snapshot()` lee una sola variable de entorno,
sin lock y sin red, así que no hay ninguna razón para despachar el
chequeo a un hilo aparte (ese patrón existe en chatgpt-proxy porque su
snapshot hace una llamada real al proveedor; copiarlo acá sería cargo
cult). Está montado como primera línea de `POST /v1/images/generations`
(`images`), `POST /v1/audio/speech` (`audio_speech`), `POST
/v1/audio/transcriptions` (`audio_transcription`) y los dos endpoints
`/v1/conversations*` (`conversations`).

Los cuatro handlers `/v1/files*` **no** llaman a `require_capability`, y es
a propósito — no porque estén exentos del gate, sino porque cada uno ya
produce el resultado que el gate garantiza (`501` inmediato, sin fallar
adentro de un gRPC) con un mensaje más específico que el genérico del
gate:

- `POST /v1/files` (`create_file`) responde `501` directamente con
  `_FILE_CREATE_REDIRECTED`, que nombra `POST /grok/files` como el lugar
  real donde vive la subida.
- `GET /v1/files` y `GET /v1/files/{id}` responden `501` directamente con
  `_FILES_REGISTRY_UNVERIFIED`, que nombra el namespace `AssetRepository`
  sin verificar y la prueba en vivo que lo resolvería.
- `DELETE /v1/files/{id}` captura `FileDeleteNotSupported` (que
  `grok_backend.delete_file` levanta sin intentar ningún gRPC) y responde
  `501` con `_FILE_DELETE_UNVERIFIED`.

Apilar `require_capability("files")` delante de cualquiera de los cuatro
no cambiaría el código de estado — `files` es `false` de forma
incondicional, así que el gate también dispararía siempre, con o sin
sesión — pero sí taparía estos mensajes específicos y accionables con el
mensaje genérico del gate, y rompería los tests de
`tests/test_files.py` que verifican ese texto específico.

`GET /health` es la referencia que no se desactualiza; esta tabla es la
referencia humana.
