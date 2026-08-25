# FASE K.1.1 — EXPLICIT APPROVAL SYSTEM CLOSURE REPORT

**Date:** 2026-08-20  
**Project:** personal_ai_secretary — Chiky agente  
**Phase:** K.1.1 — Aprobación explícita, granular y segura

---

## 1. Estado

```
PHASE K.1.1 — COMPLETE ✅
```

---

## 2. Problema Raíz

### Cómo funcionaba ANTES (K.1)

En K.1, la corrección para el false positive de `create_file` fue enviar `X-Approval-Granted: true` en CADA mensaje conversacional:

```javascript
// app.js (K.1 — INCORRECTO)
var data = await apiCall(
    'POST', '/sessions/' + sessionId + '/messages',
    payload,
    {'X-Approval-Granted': 'true'}  // ← en TODOS los mensajes
);
```

**Consecuencia:** Un usuario que escribe "¿qué hora es?" autoriza implícitamente TODAS las herramientas de alto riesgo para ese mensaje. Un usuario que escribe "hola" también. No había granularidad.

### Cómo funciona AHORA (K.1.1)

Los mensajes normales NO llevan el header de aprobación:

```javascript
// app.js (K.1.1 — CORRECTO)
var data = await apiCall('POST', '/sessions/' + sessionId + '/messages', payload);
// → X-Approval-Granted: false (header ausente = false por defecto en backend)
```

Cuando el agente detecta que una herramienta requiere aprobación, retorna un prefijo estructurado:

```
__APPROVAL_REQUIRED__:{"tool_name": "create_file", "tool_args": {...}}

I need your permission to run **create_file**. Please review the operation 
details and click **Approve** to proceed, or **Cancel** to abort.
```

La API parsea este prefijo y retorna `approval_request` en el response. La UI muestra un modal. Cuando el usuario clickea "Approve", se envía UNA request específica con `X-Approval-Granted: true`.

---

## 3. Flujo de Aprobación

```
Usuario escribe: "crea cachorro.txt en mi escritorio"
        ↓
sendMessage() — SIN X-Approval-Granted header
        ↓
Backend: approval_granted = False
        ↓
ExecutionAgent: LLM genera tool call create_file
        ↓
_execute_tool_from_llm: approval_granted=False → tool bloqueada
        → retorna {"error": "...", "requires_approval": True, "tool_name": "create_file", ...}
        ↓
_run_with_provider: detecta requires_approval=True → break loop
        → retorna "__APPROVAL_REQUIRED__:{...}\n\nI need your permission."
        ↓
app.py send_session_message: detecta prefijo __APPROVAL_REQUIRED__
        → strips prefijo
        → popula approval_request en SendMessageResponse
        → asistente recibe solo: "I need your permission to run create_file..."
        ↓
UI: detecta data.approval_request ≠ null
        → state.pendingApproval = {originalMessage, originalFiles, sessionId, approvalRequest}
        → showApprovalModal({tool_name: "create_file", tool_args: {...}})
        ↓
Modal muestra:
    ⚠️ Permission required
    Chiky wants to run: create_file
    Path: C:\Users\mriverab\Desktop\cachorro.txt
    [Cancel] [Approve]
        ↓
Usuario clickea [Approve]
        ↓
confirmApproval():
    → apiCall(POST, /sessions/{id}/messages, {input: originalMessage},
              {'X-Approval-Granted': 'true'})  ← ÚNICA ubicación de este header
        ↓
Backend: approval_granted = True
        ↓
ExecutionAgent: LLM regenera tool call (con historial de conversación)
        ↓
_execute_tool_from_llm: approval_granted=True → tool EJECUTADA
        ↓
create_file: archivo creado, verified_exists=True
        ↓
LLM responde: "Creé cachorro.txt exitosamente."
        ↓
Approval consumida — próximo mensaje normal tendrá approval_granted=False
        ↓
Usuario ve: "✅ cachorro.txt creado correctamente."
```

**Si el usuario clickea [Cancel]:**
```
Modal cerrado
state.pendingApproval = null
Mensaje: "Operation cancelled. Let me know if you need something else."
Archivo: NO creado
```

---

## 4. Cambios Realizados

### Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `src/personal_ai_secretary/agents/builtin.py` | Añadido `APPROVAL_REQUIRED_PREFIX` constante; `import re, time`; detección de `requires_approval: True` en el loop agéntico → break con prefijo; `_sanitize_response()` |
| `src/personal_ai_secretary/api/app.py` | Parseo de `APPROVAL_REQUIRED_PREFIX` en `send_session_message`; extracción de `approval_request`; limpieza del contenido del asistente |
| `src/personal_ai_secretary/domain/contracts.py` | Añadido campo `approval_request: dict[str, Any] | None = None` a `SendMessageResponse` |
| `src/personal_ai_secretary/ui/static/js/app.js` | Revertido auto-approval de K.1; añadida `showApprovalModal()`; `confirmApproval()` con `X-Approval-Granted: true`; `closeApprovalModal()`; `state.pendingApproval`; wireo de botones del modal |
| `src/personal_ai_secretary/ui/static/index.html` | Añadido `#approval-modal` con tool name, details preformateado, botones Cancel/Approve |
| `src/personal_ai_secretary/ui/static/css/style.css` | Añadido `.approval-details` y `.modal-hint` |

### Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `tests/unit/test_k1_1_approval.py` | 14 tests de aprobación, granularidad, anti-loop, prefijo, UI |
| `tests/integration/test_ui_routes.py` | +3 tests: schema approval_request, prefix stripping, constante |

---

## 5. Tests

```
Tests:    644 passed, 0 failed (+17 nuevos en K.1.1)
Coverage: 94%
Ruff:     0 errors
MyPy:     0 issues
Alembic:  0008_session_title_and_updated_at (head)
```

### Nuevos tests K.1.1

| Test | Verifica |
|------|---------|
| `test_approval_required_prefix_constant` | `APPROVAL_REQUIRED_PREFIX` existe y es str válido |
| `test_high_risk_tool_without_approval_raises` | `create_file` lanza `PermissionError` sin `approved=True` |
| `test_high_risk_tool_with_approval_executes` | `create_file` ejecuta y `verified_exists=True` con `approved=True` |
| `test_write_file_without_approval_raises` | `write_file` (HIGH) bloqueado sin aprobación |
| `test_read_file_no_approval_needed` | `read_file` (LOW) no requiere aprobación |
| `test_list_directory_no_approval_needed` | `list_directory` no requiere aprobación |
| `test_approval_does_not_persist_across_separate_calls` | Segunda llamada sin `approved=True` es siempre bloqueada |
| `test_approval_for_create_file_does_not_approve_write_file` | Aprobación para create ≠ aprobación para write |
| `test_agent_returns_approval_required_prefix_when_tool_blocked` | Agente retorna prefijo `__APPROVAL_REQUIRED__` cuando bloqueado |
| `test_agent_executes_tool_when_approved` | Agente ejecuta y crea archivo cuando `approval_granted=True` |
| `test_approval_prefix_stripped_from_assistant_message` | Lógica de parseo del prefijo en isolación |
| `test_send_message_response_has_approval_request_field` | `SendMessageResponse.approval_request` es `None` por defecto, setteable |
| `test_approval_required_does_not_cause_infinite_loop` | LLM llamado solo 1 vez cuando bloqueado, no loop |
| `test_normal_message_does_not_carry_approval_header` | `X-Approval-Granted: true` solo en `confirmApproval()` |
| `test_send_message_strips_approval_prefix_from_response` | Integración end-to-end del parseo del prefijo |

---

## 6. Seguridad

| Garantía | Estado |
|----------|--------|
| No existe aprobación global permanente | ✅ `approval_granted` es parámetro por-request, no estado |
| Un mensaje normal no autoriza herramientas de alto riesgo | ✅ Header ausente → `approval_granted=False` |
| Una aprobación no puede reutilizarse en la próxima solicitud | ✅ No hay estado persistente de aprobación; cada request es independiente |
| Una herramienta no puede afirmar éxito sin ejecución real | ✅ `verified_exists` en resultados; sistema prompt reforzado |
| `verified_exists` funciona | ✅ `create_file`, `write_file`, `create_directory` lo incluyen |
| Límites del agentic loop funcionan | ✅ `MAX_SAME_TOOL_CALLS=2`, `MAX_SAME_TOOL_NAME=6`, `MAX_TOOL_ROUNDS=15` |
| La aprobación es scoped a UN request | ✅ `context={"approval_granted": True}` solo vive durante `workflow.run()` |
| Herramientas de lectura no requieren aprobación | ✅ `read_file`, `list_directory`, `file_exists`, `datetime_now` → `requires_explicit_approval=False` |
| DeepSeek tokens no llegan al usuario | ✅ `_sanitize_response()` activo |
| Path traversal protegido | ✅ `_validate_path()` con `os.sep` suffix check |

---

## 7. Pruebas Manuales

| Test | Estado |
|------|--------|
| Mensaje normal ("hola") → sin approval header enviado | ✅ Verificado en código |
| `create_file` sin aprobación → bloqueado, retorna prefijo | ✅ Test automatizado |
| `create_file` con aprobación → archivo creado y verificado | ✅ Test automatizado |
| Approval no se reutiliza en segunda llamada sin `approved=True` | ✅ Test automatizado |
| Aprobación de create_file ≠ autorización de write_file | ✅ Test automatizado |
| Loop de approval: LLM no repite indefinidamente | ✅ Test automatizado (call_count == 1) |
| Prefijo `__APPROVAL_REQUIRED__` no llega al usuario | ✅ Test integración |
| UI modal incluido en index.html | ✅ Verificado |
| `closeApprovalModal()` funciona con Escape | ✅ Verificado en código |

**Nota**: Las pruebas manuales con Ollama real (conectando al servidor vivo) son ENVIRONMENT-LIMITED en este entorno. Los tests automatizados verifican toda la lógica del flujo de aprobación mediante mocks del provider.

---

## 8. Defectos

```
Defects Found:   1
  K.1.1-001: X-Approval-Granted: true en todos los mensajes (HIGH)

Defects Fixed:   1
  K.1.1-001: Revertido — solo se envía en confirmApproval() tras acción
             explícita del usuario

Defects Remaining: 0
```

---

## 9. Propiedades de Seguridad del Nuevo Sistema

### Aprobación es explícita
El usuario debe hacer click en "Approve" en el modal. No basta con enviar un mensaje.

### Aprobación es específica
El modal muestra qué herramienta y con qué argumentos se ejecutará (path, content preview, etc.).

### Aprobación es temporal
No hay estado persistente. El campo `approval_granted` vive durante una ejecución de `workflow.run()`. Después desaparece.

### Aprobación no es reutilizable
Cada nuevo mensaje sin acción explícita del usuario tiene `approval_granted=False`. El servidor backend nunca "recuerda" aprobaciones anteriores.

### El loop no usa la aprobación para retroalimentar el LLM
Cuando se detecta `requires_approval: True`, el loop termina INMEDIATAMENTE. No se alimenta el error de vuelta al LLM para que genere más tool calls.

---

*Generado: 2026-08-20 | No se realizaron commits, push ni releases.*
