# FASE CHAT-HANG-01 — ROOT CAUSE

## Veredicto

**ROOT CAUSE CONFIRMADA a nivel de flujo/sesión — NO se encuentra en el código actual del
pipeline (frontend, backend o provider).** El mensaje reportado jamás entró al pipeline y la
UI mostraba un JS anterior al fix sin límite temporal.

## Cadena causal (con evidencia)

### E1. El mensaje del usuario no entró al pipeline
- BD: `SELECT ... FROM requests WHERE input LIKE '%esteban%'` → **0 filas** ANTES de la
  reproducción (solo existe la fila de mi repro, created_at 2026-09-10 16:52:07.762893).
- El endpoint commitea la fila al entrar (`application/service.py:127`, `create()`); una fila
  ausente ⇒ `execute` nunca corrió ⇒ ni espera de provider posible.
- Dispatch: ambos servidores `opencode serve` del usuario (`65405a50` 16:01:45Z y `a65164a7`
  16:02:07Z = PID 19308,:51021) registraron **process=0, stream=0, session=0** en
  `~/.local/share/opencode/log/opencode.log`. Patrón idéntico al run de la mañana (`bd1fc3ae`).
- Reloj: el servidor del usuario, PID 18472 (spawn managed 16:02:07Z), está **muerto** desde
  antes de 16:44Z (0 python). Hubo **doble arranque** del backend: dos serves en 20 s
  (16:01:45Z y 16:02:07Z), ambos cerraron en <1 min.

### E2. La UI del usuario ejecutaba JS sin límite temporal
- «Cargando >30 min» es **físicamente imposible** con el `app.js` hoy servido:
  `CHAT_REQUEST_TIMEOUT_MS = 180000` (`ui/static/js/app.js:142`), `AbortController`
  (`:154-156`), `chatFailureMessage` (`:177-179`), aplicado en `sendMessage` (`:529`) y
  `confirmApproval` (`:1749`). Máximo de «Thinking…» = 180 s + mensaje de error.
- El único camino con riesgo de spinner eterno es el **JS anterior al fix cacheado** en el
  navegador (build servido por una instancia anterior), combinado con fetch a un backend que
  no contestaba (muerto o en arranque). Evidencia: E1.

### E3. Reproducción controlada: sin ventana sin-servidor, NO hay cuelgue
- Backend real + managed serve sano: «hola, mi nombre es esteban» → **HTTP 200 en 10.717 s**,
  fila `completed`, respuesta de OpenCode, dispatch process=1/stream=2 (`run c1e36889`).
- En `APP_ENV=test` (sin managed): **HTTP 200 en 4.846 s** (ruta determinística, 0 spawn).
- Ambos casos acotados de extremo a extremo. No hay ninguna operación de chat sin tope.

## Qué NO es la causa (descartado con evidencia)
- Un hang dentro de `RequestService.execute`/`GovernedWorkflow`/`provider.generate`:
  habría fila `accepted→running` en BD — no existe.
- Un `opencode serve` del usuario bloqueado: no recibió ningún dispatch (0 entrada).
- El Desktop OpenCode (Electron): proceso independiente, sin listener en 4097; no interviene.

## Naturaleza y gravedad
- Causa raíz operacional (entorno del usuario), **ya mitigada en código** por el fix previo
  de timeout/abort/UX y por el lifecycle managed (reaper de huérfanos, guard de doble spawn).
- El patrón de doble arranque + muerte rápida del backend (E1) explica por qué «esteban» cayó
  en una ventana sin servidor listo. No requiere parche de código adicional (evidencia: E3).