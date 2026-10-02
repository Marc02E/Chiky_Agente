# FASE CHAT-HANG-01 — RESULTS

Fecha: 2026-09-10 (local = UTC−6). Backend real uvicorn 127.0.0.1:8000, provider automatic
(opencode / big-pickle), managed `opencode serve`. Sin commits/pushes/merges.

## Pruebas

| # | Prueba | Método | Resultado | Evidencia |
|---|---|---|---|---|
| C | Chat real sano | POST «hola, mi nombre es esteban» con backend listo + managed serve | **PASS** — HTTP 200, `completed`, respuesta real, **10.717 s** (pipeline 10.258 s) | `logs/reproduction.json`, `logs/request_trace.json`, `logs/backend.log`, `logs/provider.log`, dispatch en `opencode.log` run `c1e36889` (process=1, stream=2, session=7, err=0) |
| B | Sin managed (APP_ENV=test) | POST en `test` (suppression de spawn) | **PASS** — HTTP 200, `completed`, **4.846 s**, 0 spawn managed | `logs/prueba_B.json`, `logs/backendB.log` |
| A | No-IA / determinística | Previo en ronda FASE-RELEASE-FINAL + suite (manual-unavailable, deterministic) | PASS (referenciado) | FASE-RELEASE-FINAL/RESULTS.md, `tests/integration/test_release_manual_unavailable.py`, `tests/unit/test_o_deterministic.py` |
| Lifecycle | Reap de huérfano | Arranque con huérfano 19308 (:51021) presente | **PASS** — 19308 reaped; managed nuevo 16224/:58096 | `logs/process_state.json` |
| JS servido | Marcadores de timeout | GET `/static/js/app.js` | **PASS** — 84 265 bytes con `CHIKY_CHAT_TIMEOUT_MS`, `CHAT_REQUEST_TIMEOUT_MS`, `chatFailureMessage`, `AbortController` | `logs/process_state2.json` (sección `served_js`) |
| Limpieza | Sin procesos tras apagado | `taskkill /T /F` del árbol uvicorn | **PASS** — 0 `opencode serve` y 0 python tras apagado | `logs/process_state2.json` |

## Timestamps clave (reproducción C)

```
T-1 uvicorn lanzado  16:51:45.942Z (PID 18840 → worker 22736, listener :8000)
T0  backend listo    16:52:06.041Z  (health/live 200)
T1  request enviado  16:52:07.332Z  (POST esteban)
T10 respuesta        16:52:18.049Z  (HTTP 200) — ms_total 10 717
    DB created_at    16:52:07.762893 → updated_at 16:52:18.020757 (pipeline 10.258 s)
    Dispatch         run c1e36889 init 16:52:02.370Z — process=1 stream=2 session=7 err=0
Fila DB (única)      status=completed, respuesta real de OpenCode
```

## Evidencia del escenario del usuario (14:00Z–17:00Z)

| Run | Inicio | Fin actividad | process/stream/session | Interpretación |
|---|---|---|---|---|
| bd1fc3ae | 14:48:40Z (mañana) | 14:49:42Z | 0/0/0 | Backend matinal, nada despachado |
| 65405a50 | 16:01:45Z | 16:02:48Z | 0/0/0 | 1er arranque (doble start) |
| a65164a7 | 16:02:07Z | 16:03:07Z | 0/0/0 | Serve huérfano del backend 18472: **nada llegó a OpenCode** |
| c1e36889 | 16:52:01Z (repro) | 16:53:02Z | 1/2/7 | Dispatch real de esteban en la reproducción |

BD antes de la repro: **0 esteban** (requests y sessions). `provider_settings` intacto
(ai_provider=auto, routing_mode=automatic, selected_provider=opencode, selected_model=big-pickle).

## Veredicto

- **ROOT CAUSE CONFIRMADA (flujo/sesión), no de código**: esteban surgió en una ventana sin
  backend listo (doble arranque + backend muerto) y con JS pre-fix en caché en el navegador
  (única vía a un spinner >30 min; el app.js actual aborta a los 180 s). Detalle en
  `ROOT-CAUSE.md` (E1–E3).
- **Ninguna operación de chat queda sin límite temporal** con el código actual (verificado
  C=10.7 s, B=4.8 s, JS=180 s, provider=240 s).
- **No se declara RELEASE VERIFIED** (mandato). Pendiente del usuario: Recargar con Ctrl+F5 y
  validar un mensaje real desde la UI (esteban; opcional fibonacci.py y CRUD).
- **NO COMMIT / NO PUSH / NO MERGE / NO REBASE**

## Entregables
`FASE-CHAT-HANG-01/DIAGNOSIS.md`, `ROOT-CAUSE.md`, `FIX.md`, `RESULTS.md`,
`logs/{reproduction.json, request_trace.json, process_state.json, process_state2.json,
prueba_B.json, backend.log, backend2.log, backendB.log, provider.log}`.