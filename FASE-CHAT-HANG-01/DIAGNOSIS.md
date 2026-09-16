# FASE CHAT-HANG-01 — DIAGNOSIS

Fecha: 2026-09-10 (UTC−6, Ciudad de México)
Mensaje reportado: «hola, mi nombre es esteban» — UI cargando >30 min (sin respuesta).

## Objetivo

Confirmar con evidencia observable si el cuelgue de +30 min proviene de un punto concreto
del sistema (frontend, backend, provider/OpenCode) o si no se reproduce con el código
actual. Toda conclusión con logs, timestamps, PIDs, filas de BD y dispatch reales.

## Metodología

- Traza de extremo a extremo con timestamps (T0 listo backend → T10 respuesta).
- Exec inicial: sin servidor externo en 8000, sin matar procesos ajenos (solo se usó el
  reclaim del propio app para el huérfano gestionado).
- Reproducción mediante backend real (`run_local.ps1` equivalente: uvicorn en 127.0.0.1:8000)
  y POST HTTP con JWT al endpoint real de mensajes.
- Evidencia recogida en `logs/`: `reproduction.json`, `request_trace.json`,
  `process_state.json`, `process_state2.json`, `prueba_B.json`, `backend.log`,
  `backend2.log`, `backendB.log`, `provider.log` (= stdout del `opencode serve` managed).

## Estado inicial (16:44–16:51Z)

| Ítem | Hallazgo |
|---|---|
| Puerto 8000 | Libre (ningún backend sirviendo) |
| Procesos python | 0 (el backend del usuario del turno anterior, PID 22828/22144, ya no existe) |
| Backend del usuario | PID 18472 (uvicorn, arrancó ~16:02Z) — **muerto**; dejó `opencode.exe serve --port 51021` PID 19308 con PPID 18472 muerto => **huérfano activo** |
| Desktop OpenCode | PID 17736 (Electron, independiente de Chiky; no atiende 4097) |
| BD `requests`/`sessions` | **0 filas** conteniendo «esteban» |
| `opencode.log` hoy | run `65405a50` (init 16:01:45Z) y run `a65164a7` (init 16:02:07Z; = serve 51021/19308): ambos **process=0, stream=0, session=0** (nada despachado). Runtimes de la mañana (`bd1fc3ae`) también 0 dispatch |

Conclusión del estado inicial: el mensaje del usuario **nunca entró al pipeline**
(`service.send_message` → `RequestService.create()` commitea en `application/service.py:127`;
sin fila ⇒ el endpoint no llegó a ejecutar create, o el backend no servía cuando se envió).

## Hallazgos por capa

### Frontend (FASE 3)
- `src/.../ui/static/js/app.js` (servido hoy, 84 265 bytes) contiene el fix completo:
  - `:141-145` `CHAT_REQUEST_TIMEOUT_MS = 180000` + override `window.CHIKY_CHAT_TIMEOUT_MS`.
  - `:154-156` `AbortController` + `setTimeout` en `apiCall`.
  - `:177-179` `chatFailureMessage` ("request timed out").
  - `:509` / `:529` `sendMessage` usa `timeoutMs: chatRequestTimeoutMs()`.
  - `:1749` `confirmApproval` igualmente acotado.
- Un spinner de **>30 min es imposible con este JS servido** (máximo 180 s + mensaje de error
  visible). El cuelgue de 30 min solo es posible con el **JS anterior al fix en caché del
  navegador** (sin timeout). E2E previa (FASE-RELEASE-FINAL, T1/T2) ya demostró el error UX acotado.

### Backend (FASE 4)
- Endpoint `POST /api/v1/sessions/{id}/messages` (`api/app.py:1218`) — `service.send_message` (`service.py:421`):
  `create` (commit BD) → `execute` (`service.py:172`) → `GovernedWorkflow.run` → `provider.generate`.
- Cada await tiene cota: DB (rápidos), `opencode_provider.generate` con `httpx.Timeout(5, read=240)`
  (`opencode_provider.py:47,290-321`), arranque del managed con `_READY_TIMEOUT=45`
  (`opencode_server.py:35,358`). Sin esperas sin tope en la ruta.
- El pipeline commitea la fila al ENTRAR (`service.py:127`). Sin fila ⇒ request jamás entró.

### Provider / OpenCode
- El provider usa `_server()` (`opencode_provider.py:81`): si el base_url configurado (4097) no
  responde, levanta el managed `opencode serve` (`ensure_running`/`reap_orphaned_managed`).
- En la reproducción controlada el managed respondió y **despachó** la inferencia (ver RESULTS).

## Reproducción controlada (16:52Z)

- 0 python previo; se reusa el recurso: el arranque reapó el huérfano 19308 (:51021) — el nuevo
  managed quedó en PID 16224 / puerto 58096 (hijos de uvicorn 18840→22736).
- `POST .../messages` con «hola, mi nombre es esteban»: **HTTP 200 en 10.717 s**,
  fila `requests` status `completed`, respuesta real de OpenCode, dispatch visible en
  `opencode.log` run `c1e36889` (process=1, stream=2, session=7, err=0).

## Conclusión

El cuelgue de >30 min NO se reproduce con el pipeline/servidor actual; la cadena de
evidencia apunta a una ventana en que **no había backend listo sirviendo** la UI/API y a un
**JS pre-fix en caché** en el navegador del usuario. Detalles en `ROOT-CAUSE.md`.