# FIX — FASE CHAT-HANG-02

**Cambio mínimo, solo con causa demostrada. Sin tocar Router, OpenCode, provenance,
fallback, selección de provider ni Modo Manual/Automático.**

## Archivo modificado
`scripts/launch.py` (2 ediciones).

## Antes
`scripts/launch.py:116-117`:
```python
stdout=subprocess.PIPE,
stderr=subprocess.STDOUT,
```
Pipe anónimo que nadie lee → búfer lleno → uvicorn bloqueado escribiendo logs → API
inoperante.

## Después
`scripts/launch.py`:
```python
stdout=_open_server_log(project_root),
stderr=subprocess.STDOUT,
```
y helper nuevo:
```python
def _open_server_log(project_root: Path):
    """Open an append-only file for the server's stdout/stderr.

    Writes to a real file instead of an undrained pipe: a pipe whose buffer
    fills would block the uvicorn process writing its logs, leaving the API
    unresponsive (UI hangs, messages never answered).
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    return open(log_dir / "backend.log", "ab")
```

Nada más cambia (readiness, timeout, apertura del browser, shutdown, port-check intactos).
El backend ahora escribe sus logs a `logs/backend.log` (append), se conservan los logs y
ya no existe un pipe que pueda bloquear al proceso.

## Por qué es mínimo y suficiente
- Es el único punto donde se crea el pipe sin drenar (`grep -n PIPE` en `scripts/` solo
  coincide aquí).
- No introduce dependencias nuevas, ni hilos, ni cambios de shutdown.
- Resuelve la causa primaria por eliminación del mecanismo (ya no hay pipe que llenar).

## No aplicado (fuera de alcance de FASE 9 "mínimo")
- **Shutdown armonioso del serve huérfano:** la muerte dura del uvicorn (cierre de
  consola / terminate) sigue pudiendo dejar `opencode.exe serve` huérfano. Mitigación ya
  activa en la app (reap de huérfanos al arrancar). Como hardening futuro se podría
  enviar `CTRL_BREAK_EVENT` en Windows para un graceful shutdown de uvicorn, o que el
  launcher reapree al inicio. **No** está implementado aquí (requiere validación en
  consola interactiva y ampliaría el alcance).
- No hay cambios en la UI, rutas del backend o esquemas de BD.

## Validación (FASE 10)
Ejecución íntegra a través del launcher corregido (`python scripts/launch.py --no-browser`)
con smoke real _"hola, mi nombre es esteban"_. 15/16 puntos OK en laboratorio; detalle en
RESULTS.md. Punto 16 matizado: el cierre del harness usó un terminate duro (no el Ctrl+C
real del usuario); el reap de huérfanos por parte de la app quedó verificado
independientemente (huérfano 14572 reapeado al arrancar; 0 serves tras la validación).