# FASE CHAT-HANG-02 — DIAGNOSIS

**Contexto:** Fase CHAT-HANG-02. Diagnóstico con evidencia (no supuestos) de la causa raíz
real de los síntomas reportados en la UI, mientras los tests HTTP/controlados funcionan.

Síntomas del usuario:
1. Desde la UI, enviar _"hola, mi nombre es esteban"_ → sin respuesta.
2. La UI no permite eliminar conversaciones.
3. Recargar la página hace "caer" al backend.
4. Los tests HTTP/controlados funcionan.

## Procedimiento (fases ejecutadas)

| Fase | Actividad | Resultado |
|---|---|---|
| 1 | Inspección sin tocar código: procesos, sockets, parents, endpoints | Sin backend; 1 serve huérfano del usuario (PID 14572, `serve --port 59030`, parent 22484 muerto) + árbol OpenCode Desktop |
| 2 | Reproducción browser (Playwright) con 2 backends (sano vs pipe de `launch.py`) | Contraste total: sano todo funciona; pipe se cuelga |
| 3 | Delete desde UI | Endpoint DELETE existe y responde 204; DB decrece en 1 |
| 4 | Reload + clasificación de muerte | Backend sano sobrevive 4 recargas (PID vivo, health 200); con pipe, la app se bloquea (clasificación E) |
| 5 | Contrastación app.js (rutas) vs backend | Rutas de la UI correctas y presentes (`/api/v1/ui/sessions`: GET/PATCH/DELETE existen) |
| 6 | Sesiones/conversaciones (FK, cascade, títulos) | Sin defectos: listado 200, delete borra requests+sesión |
| 7 | Mapeo chat → cadena A..H | A.-H. todo presente en backend sano |
| 8 | TEST A/B/C (≤30s cada uno) | Todos pasan sobre backend sano |
| 9 | Cambio mínimo con causa demostrada | `launch.py`: stdout PIPE sin drenar → archivo de log |
| 10 | Validación 16 puntos + smoke real | 15/16 (punto 16 limitado a entorno del harness; mitigación existente verificada) |

## Hallazgos

### El backend y la UI son correctos (evidencia de control)
Con uvicorn lanzado con **stdout a archivo** (sin pipe), sobre la misma base de datos y
procesos, un browser headless (Chromium) navegó y ejecutó el flujo real:

- **Boot:** `GET /api/v1/ui/sessions` → **200**, sidebar carga 50 conversaciones.
- **TEST A** (nueva conversación → _"hola, mi nombre es esteban"_) → **respuesta 200 en 6.95 s**,
  con burbuja de asistente: `Hola Esteban! En qué te puedo ayudar hoy?` + etiqueta
  `Ejecutado por: opencode · big-pickle · OK · 6533ms`.
- **TEST B** (reload → misma conversación → _"hola"_) → **200 en 25 s**, conversación restaurada.
- **TEST C** (menú contextual → Delete → confirmar) → **DELETE /api/v1/ui/sessions/{id} → 204**,
  total de sesiones en BD **370 → 369**.
- **4 recargas consecutivas**: health `/api/v1/health/live` → **200** en cada una, proceso con el
  mismo PID (vivo). El reload NO mata el backend sano.
- Delete + Rename existen como rutas del router `ui` (`src/personal_ai_secretary/ui/routes.py`).

### La causa la mete el lanzador (reproducción con la firma exacta de `scripts/launch.py`)
`scripts/launch.py:116` lanza uvicorn con:

```python
stdout=subprocess.PIPE,   # <== nadie lee este pipe
stderr=subprocess.STDOUT,
```

y en todo el programa nadie hace `read` del pipe (solo `server_proc.wait()`). En Windows un
pipe anónimo tiene un búfer finito: cuando se llena, el proceso hijo que escribe (uvicorn
volcando sus access-log con `--log-level info`) **se bloquea en el `write()`**, dejando de
atender el socket HTTP.

Reproducción determinista (lab, uvicorn idéntico a `launch.py`):
- Rafaga de requests: **requests 1–19 → 200 (3–5 ms)**; **request 20 → timeout de 4 s, sin
  recuperación**. El servicio quedó bloqueado.
- Drenar el pipe (leerlo desde el lado padre) **desbloquea** al proceso al instante.

Reproducción en browser (mismo pipe sin drenar, 0 pre-carga):
- Boot del SPA: `GET /api/v1/providers/config`, `GET /api/v1/ui/sessions` enviados pero
  **nunca completan** → la UI queda "cargando", `#chat-input` inaccesible.
- Con un poco de margen antes: envío n.º 1 → **200 en 609 ms**; envío n.º 2
  (_"hola, mi nombre es esteban"_) → **colgado (timeout 6 s, sin respuesta)**;
  **al drenar el pipe, ese mismo request colgado completa con 200**. La petición estaba
  "en el backend", bloqueada por el pipe.

Esto explica y unifica los 3 síntomas del usuario:
1. **Sin respuesta** → el backend está bloqueado escribiendo en un pipe lleno, no procesa.
2. **No permite eliminar** → con el backend bloqueado, `/api/v1/ui/sessions` no responde,
   el sidebar no carga y no hay conversaciones sobre las que abrir el menú; además toda
   acción (Delete/Rename) depende de un backend que no responde. El endpoint DELETE en sí
   existe y funciona (204). No es un defecto de la UI.
3. **Recargar "cae" al backend** → el reload lanza más requests → más access-log → acelera y
   profundiza el bloqueo del pipe; el usuario percibe "el backend se cayó". (El backend sano
   sobrevive al reload: 4/4 OK.)

Y explica por qué **los tests HTTP/controlados sí funcionan**: todos lanzaban uvicorn con
stdout a archivo o a consola, nunca a un pipe sin drenar.

### Causa secundaria (observada, misma firma que los huérfanos del usuario)
Toda muerte dura del uvicorn (un `terminate()` del launcher o el cierre de la ventana de
consola = `CTRL_CLOSE`) deja huérfano al `opencode.exe serve` que el managed provider lanzó
detached. Esta es exactamente la firma observada en los dos huérfanos documentados:
- **PID 19308** `serve --port 51021` (arrancado 10:02 local, parent muerto) — CHAT-HANG-01.
- **PID 14572** `serve --port 59030` (arrancado 13:26 local, parent 22484 muerto) — esta fase.

**Mitigación ya existente y verificada:** el app reapa serves huérfanos al arrancar
(`recovery`/`ensure` del managed provider). El PID 14572 fue reapeado automáticamente al
arrancar el backend de la validación FASE 10 (tras la validación: 0 serves). Por eso los
huérfanos **no** son bloqueantes para la UI.

### Lo que NO es la causa (descartado con evidencia)
- **No** es OpenCode: con la cadena completa sana, el dispatch a opencode funciona (run
  `c1e36889`, dispatch real, respuesta 6.95 s, provenance `opencode / big-pickle`).
- **No** es caché del navegador: el hash del JS servido coincide con el archivo local; el
  comportamiento se reproduce con Chromium limpio (headless).
- **No** es la UI: las rutas `/api/v1/ui/sessions` (GET/PATCH/DELETE) existen y responden 200/204.
- **No** es la base de datos: listado, títulos, delete y conteos correctos.
- **No** es la configuración de provider: `provider_settings` intacto
  (`ai_provider=auto`, `routing_mode=automatic`, `selected_provider=opencode`,
  `selected_model=big-pickle`).

## Evidencia cruda

Carpeta `FASE-CHAT-HANG-02/logs/`:
- `pipe_deadlock.json` — rafaga 19 OK + 1 timeout (colgado de pipe).
- `browser_trace.json` — TEST A/B/C + boot + 4 recargas (backend sano).
- `chat_trace.json`, `delete_trace.json`, `reload_trace.json` — sub-traces.
- `pipe_browser_trace.json` — envio n.º1 200 (609 ms), envio n.º2 colgado, drenar
  → el colgado completa 200.
- `fase10_validation.json` — validación de 16 puntos con launcher corregido.
- `launcher.log`, `backend.log` (en `logs/` del repo), `pipe_dump_B.log`.

SEO: entrada "chata-hol-doce-fase-02" no aplicable.