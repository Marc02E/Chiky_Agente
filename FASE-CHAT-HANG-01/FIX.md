# FASE CHAT-HANG-01 — FIX

## Decisión

**No se ejecutó ningún cambio de código en esta FASE** (principio de no parches especulativos:
el pipeline, el provider y la UI ya están acotados y quedó demostrado por reproducción).
El fix del cuelgue de 30 min ya está presente en el árbol y en el build servido (ver la
tabla de evidencia en RESULTS). Esta FASE aporta la **confirmación diagnóstica** y la
**documentación operativa** para evitar el escenario del usuario.

## Estado ya corregido (verificado aquí)

| Capa | Fijación | Evidencia |
|---|---|---|
| UI | Timeout 180 s + AbortController + mensaje de error (`app.js:142,154-156,177-179,529,1749`) | JS servido = 84 265 bytes con los 4 marcadores; E2E previa T1/T2 |
| Provider | Lectura acotada 240 s (`opencode_provider.py:47,290-321`) | Rutina controlada |
| Ciclo de vida | Reaper de huérfanos + guard de doble spawn + stop primero en shutdown (`opencode_server.py:112,208-234`) | Huérfano 19308 reaped; 0 huérfanos tras apagado controlado |
| Backend | Fila de request commiteada al entrar (`service.py:127`) ⇒ todo request es trazable | 1 fila esteban (repro) |

## Recomendaciones (no-code, para el usuario/entorno)

1. **Recargar la UI con Ctrl+F5 / Cmd+Shift+R tras una actualización** del backend. Un tab
   con el `app.js` en caché (sin timeout) es la única vía posible al spinner indefinido.
2. **No abrir dos terminales con `scripts/run_local.ps1` a la vez.** El double-arranque de
   10:01/10:02 (evidencia: dos `opencode serve` en 20 s) produjo muertes tempranas del backend.
3. Si el backend no responde, la UI actual lo indica en ≤180 s (mensaje de error + reintento);
   verificar antes de reintentar que 127.0.0.1:8000 responde (`/api/v1/health/live`).
4. **Sigue vigente** la solicitud del usuario de validar el mensaje real desde la UI
   (Recargar + esteban; opcionalmente fibonacci.py y CRUD) antes de cualquier declaración
   final de release — no se declara «RELEASE VERIFIED» en esta FASE.

## Follow-up propuesto (fuera de alcance, sin parche hoy)
- Diferencial de topes: UI 180 s vs provider read 240 s, y loops agénticos multiturno sin
  límite TOTAL. Con un turno frío lento (>180 s) la UI abortaría antes que el backend
  (request queda `running` y luego `completed`). Recomendación: alineación de timeout global
  opcional por request cuando exista evidencia de un caso real.
- Loguear el arranque del lifespan en archivo (los redirect de consola de uvicorn quedan en 0
  bytes con Start-Process; aquí se usó logging a archivo directo en el manejo del driver de
  reproducción).