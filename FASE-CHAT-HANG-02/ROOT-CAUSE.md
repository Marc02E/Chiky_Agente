# ROOT CAUSE — FASE CHAT-HANG-02

**Veredicto: ROOT CAUSE CONFIRMED (en el lanzador, no en la aplicación/web).**

## Causa raíz primaria
`scripts/launch.py` lanza uvicorn con `stdout=subprocess.PIPE` (y `stderr=STDOUT`) y
**nunca lee el pipe**. Al volcar uvicorn sus access-log con `--log-level info`, el búfer
finito del pipe anónimo de Windows se llena; el `write()` de uvicorn se **bloquea**, el
proceso deja de atender el socket HTTP y la aplicación queda **inoperante** (chat sin
respuesta, sidebar sin carga, delete/rename imposibles, reload que "mata" el servicio).

### Cadena causal (con evidencia)
1. `launch.py:116-117` → `stdout=PIPE`, `stderr=STDOUT`; sin reader. (código leído)
2. Cada request (acceso a `/health/live`, `/ui/sessions`, POST `/messages`, etc.) genera
   una línea de access-log escrita al stdout → llena el pipe. (reproducción)
3. Pipe lleno → uvicorn bloqueado en `write()` → no procesa más conexiones entrantes.
   (medido: requests 1–19 → 200 en 3–5 ms; request 20 → timeout y sin recuperación)
4. Browser: boot del SPA no completa (`/ui/sessions` pendiente) y/o un envío queda colgado
   (`"hola, mi nombre es esteban"` sin respuesta). (pipe_browser_trace.json)
5. Drenar el pipe **desbloquea** al backend y el request colgado completa 200. El proceso
   nunca se cayó: solo estaba bloqueado escribiendo. (clasificación: E «bloqueado», no crash)

### Resolución de los síntomas
| Síntoma | Explicación con la causa primaria |
|---|---|
| Sin respuesta en la UI | Backend bloqueado por el pipe; el POST nunca se tramita |
| No permite eliminar conversaciones | Sidebar no carga (`/ui/sessions` nunca responde) y el backend no responde al DELETE — el endpoint DELETE existe y funciona (204) |
| Reload "cae" al backend | El reload añade requests/logs → agota el búfer → el servicio se bloquea; se percibe como caída. Backend sano: sobrevive 4/4 recargas |
| Tests HTTP/controlados OK | Lanzaban uvicorn con stdout a archivo/consola, nunca a un pipe sin drenar |

## Causa secundaria (misma familia de defectos del lanzador)
Una terminación dura del uvicorn (terminate() del launcher o CTRL_CLOSE al cerrar la
consola) mata abruptamente al proceso y deja huérfano al `opencode.exe serve` del managed
provider (lanzado detached). Firmas observadas: PID 19308 (:51021, 10:02 local) y PID
14572 (:59030, 13:26 local).

No es bloqueante: el app **reapa serves huérfanos al arrancar** (verificado ahora: el
huérfano 14572 desapareció al arrancar el backend de la validación; 0 serves tras ella).

## Eliminados (con evidencia)
- ✖ defecto en backend: `/api/v1/ui/sessions` GET 200, PATCH (renombrar) existe, DELETE 204 + decremento DB.
- ✖ defecto en JS: rutas correctas (`API='/api/v1'`, `'/ui/sessions'`, `'/sessions/{id}/messages'`), JS servido == archivo local.
- ✖ fallo de OpenCode: dispatch real OK (6.95 s, provenance `opencode / big-pickle`).
- ✖ caché del navegador: reproducción en Chromium limpio (headless).
- ✖ base de datos: listado, títulos y conteos correctos.

## Definición de corrección
Dirigir stdout/stderr del subproceso a un **archivo de log** (append), eliminando por
completo el pipe sin drenar. No toca Router, OpenCode, provenance, fallback, selección de
provider ni Modo Manual/Automático.