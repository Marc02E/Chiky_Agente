# RESULTS — FASE CHAT-HANG-02

Fecha de corridas: 2026-09-10 (local −6, BD en UTC). Evidencia cruda en `FASE-CHAT-HANG-02/logs/`.

## 1) Reproducción del deadlock (H1 — pipe sin drenar, firma exacta de `launch.py`)

`chatyhang_pipe.py` → `pipe_deadlock.json`
- Spawn: uvicorn con `stdout=PIPE`, `stderr=STDOUT`, sin reader (igual que `launch.py:116`).
- Readiness: **OK en 18.76 s** (el pipe no se llena en startup en este conteo).
- Rafaga (intervalo 0.35 s):
  - requests **1–19 → HTTP 200, 3–5 ms**.
  - request **20 → timeout (error "timed out", cap cliente 4 s), sin recuperación**.
- Conclusión: el servicio quedó **bloqueado escribiendo al pipe lleno** (clasificación E).

## 2) Reproducción en browser (Parte A — backend SANO, stdout a archivo)

`chatyhang_browser.py` → `browser_trace.json`, `chat_trace.json`, `delete_trace.json`, `reload_trace.json`

| Punto | Resultado |
|---|---|
| Boot SPA | `/api/v1/providers/models|config|transparency`, `/api/v1/ui/sessions` → **200**; `boot_failed: []` |
| TEST A (nueva conv → _"hola, mi nombre es esteban"_) | **200 en 7.3 s** |
| TEST B (reload → misma conv → _"hola"_) | Backend vive tras reload (health 200); 50 items restaurados; **200** en 25 s |
| TEST C (menú → Delete → confirmar) | **DELETE /api/v1/ui/sessions/{id} → 204**; BD **370 → 369** |
| Resistencia reload (×3 más) | health **200** en todas; PID del proceso vivo (`poll()` = None = corriendo) |

## 3) Reproducción en browser (Parte B — backend con PIPE sin drenar)

`pipe_browser_trace.json`
- Boot: completa (con margen) y `#chat-input` accesible.
- Envío n.º 1 → **200 en 609 ms**.
- Envío n.º 2 (mismo mensaje) → **colgado** (sin respuesta en 6 s).
- **Se drena el pipe → el request colgado completa con 200** (20:10:10 UTC).
- Health tras drenar: **200**. El proceso nunca 500/400: estaba bloqueado.

## 4) Causa secundaria — huérfanos serve
- Estado inicial de fase: serve huérfano del usuario **PID 14572** (`serve --port 59030`,
  parent 22484 muerto, creado 13:26:24 local) — misma firma que el huérfano 19308 (:51021,
  10:02 local) documentado en CHAT-HANG-01.
- Terminación dura del backend (harness) → el serve detached queda huérfano (evidenciado en
  la corrida FASE 10 antes de la limpieza).
- **Reap verificado:** al arrancar el backend de la validación, el app reapeó al huérfano
  14572; tras la validación final: **0 procesos `opencode.exe serve` y puerto 8000 libre**.

## 5) Validación FASE 10 — launcher CORREGIDO (`launch.py --no-browser`)

`fase10_validation.json`

| # | Punto | Estado |
|---|---|---|
| 1 | Arranque vía launch.py (corregido) | ✅ ready en 15.1 s |
| 2 | Readiness del launcher ("Server is ready") | ✅ |
| 3 | `/api/v1/health/live` → 200 | ✅ (4 ms) |
| 4 | `/api/v1/ui/sessions` → 200 al boot | ✅ |
| 5 | Smoke _"hola, mi nombre es esteban"_ → respuesta ≤30 s | ✅ **6.95 s** |
| 6 | Burbuja de asistente con texto | ✅ `Hola Esteban! …` |
| 7 | DB: fila nueva con resultado | ✅ `status=completed`, result `Hola Esteban! …` |
| 8 | Provenance (requested/selected/attempted/executed) | ✅ `opencode · big-pickle · OK · 6533ms` (transparency 200) |
| 9 | Reload: PID vivo + health 200 | ✅ |
| 10 | Conversación restaurada (50 items) | ✅ |
| 11 | Envío de nuevo → 200 ≤25 s | ✅ |
| 12 | Delete → **204** | ✅ |
| 13 | DB tras delete (−1) | ✅ 371 → 370 |
| 14 | Martilleo 60 requests (≈4× el umbral que colgaba) | ✅ **0 fallos** |
| 15 | `logs/backend.log` crece (stdout a archivo) | ✅ 15,910 bytes |
| 16 | Cierre sin procesos Chiky/huérfanos | ⚠️ matizado (ver abajo) |

Punto 16: el harness detuvo al launcher con terminate duro, no con Ctrl+C (que es como
cierra el usuario); por eso quedaron el uvicorn y su serve de la corrida — ya limpiados.
El reap de huérfanos por parte de la app quedó verificado por separado (punto 4). En uso
real con Ctrl+C, el launcher ejecuta su `finally`.

## 6) Estado final del sistema (post-fase)
- Puerto **8000 libre**; **0 procesos Chiky** (python/uvicorn/opencode serve) del backend.
- Árbol del usuario intacto (OpenCode Desktop, procesos free-claude-code ajenos).
- `logs/backend.log` en raíz del repo apunta a la nueva salida del launcher (creado por el
  fix). No se elimina nada más.

## TESTS DE FASE 8 (resumen)
- TEST A (nueva conv → hola): ✅ 200 en 7.3 s (backend sano).
- TEST B (reload → misma conv → hola): ✅ 200 en 25 s.
- TEST C (nueva → delete → reload): ✅ 204 + DB decrement; reload tras delete OK.
- Límite 30 s respetado en todos.

## Notas
- Sin commits. Sin push/merge/rebase. Sin RELEASE VERIFIED (condición del mandato).
- FASE-CHAT-HANG-01 intacta (referencia de la cadena de sesión).
- La corrida FASE 10 confirmó además la causa #2 secundaria (terminar el uvicorn de golpe
  deja serve huérfano), cuya mitigación ya existe en la app (reap al arrancar).