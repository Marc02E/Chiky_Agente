# FASE K.2 — CIERRE: Development Agent Tools

**Fecha**: 2026-08-20
**Estado**: COMPLETADA

---

## Resumen

Chiky evolucionó de un agente con herramientas de archivos a un **agente de desarrollo completo** capaz de analizar, leer, modificar y verificar proyectos de software multi-archivo. Se preservaron todas las garantías de seguridad existentes.

---

## Archivos modificados/creados

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `src/personal_ai_secretary/tools/development.py` | **NUEVO** | 5 herramientas: analyze_project, read_files, modify_file, search_files, verify_files |
| `src/personal_ai_secretary/tools/builtin.py` | Modificado | Registra herramientas de desarrollo via `register_development_tools()` |
| `src/personal_ai_secretary/tools/prompt.py` | Modificado | Parámetro `model_name`, guías por modelo (Llama/DeepSeek/Qwen/Mistral), sección Development Agent Workflow |
| `src/personal_ai_secretary/tools/registry.py` | Modificado | Soporte para `optional_arguments` en `ToolDefinition` |
| `src/personal_ai_secretary/agents/builtin.py` | Modificado | `NON_TOOL_CALLING_MODELS`, extracción XML `<tool_call>`, detección de modelo vía duck typing, hints de recuperación |
| `tests/unit/test_k2_development.py` | **NUEVO** | 61 pruebas unitarias |
| `tests/integration/test_k2_integration.py` | **NUEVO** | 13 pruebas de integración |
| `tests/unit/test_tools.py` | Modificado | Actualizado para incluir herramientas de desarrollo |

---

## Problema resuelto en esta sesión

**Bug**: La herramienta `modify_file` fallaba con "Missing argument(s): content" cuando el LLM la llamaba con `mode=replace` (que solo necesita `search` y `replacement`, no `content`).

**Causa raíz**: `argument_schema` de ToolRegistry validaba todos los keys como requeridos. No existía soporte para argumentos opcionales.

**Solución**: 
1. Agregado campo `optional_arguments: frozenset[str]` a `ToolDefinition`
2. `_validate_arguments()` ahora distingue entre required (en `argument_schema`) y optional (en `optional_arguments`)
3. Herramientas `modify_file`, `analyze_project`, y `search_files` registran sus args opcionales correctamente
4. Arreglado doble-escape de backslashes en tests de integración (usando `str()` + `json.dumps()` en lugar de helper `_p()`)

---

## Quality Gates

| Gate | Resultado |
|------|-----------|
| Ruff (linting) | ✅ 0 errores |
| MyPy (typing) | ✅ 0 errores |
| Unit tests | ✅ 61/61 pasando |
| Integration tests | ✅ 13/13 pasando |
| Full test suite | ✅ Todos pasando |
| Coverage | ✅ 92% global |
| Alembic check | ⚠️ Drift pre-existente (no causado por K.2) |

---

## Herramientas de Desarrollo (capacidades)

### analyze_project
- Escaneo recursivo de directorios
- Clasificación automática de archivos por tipo (Python, JS, config, etc.)
- Conteo de líneas, detección de imports, dependencias
- Opciones: `max_depth`, `include_content`

### read_files
- Lectura multi-archivo en una sola invocación
- Detección automática de archivos binarios
- Extracción de clases y funciones (Python)
- Manejo de encoding (UTF-8/ Latin-1 fallback)

### modify_file
- 5 modos: `replace`, `append`, `prepend`, `overwrite`, `insert`
- Búsqueda por string o regex
- Backup automático antes de modificar
- Verificación post-escritura
- **Requiere approval** (HIGH risk)

### search_files
- Búsqueda por contenido con regex
- Filtrado por extensión
- Limitación de resultados

### verify_files
- Verificación de existencia de archivos
- Validación de sintaxis Python (`ast.parse`)
- Chequeo de imports
- Validación de JSON/YAML

---

## Limitaciones conocidas

1. **Sin ejecución de shell**: No se ejecutan comandos (`npm install`, `pytest`, etc.)
2. **Sin optimización de tokens**: La sección de workflow es extensa; futuras fases optimizarán
3. **coverage de development.py**: 76% — los paths faltantes son edge-cases de encoding/binary que requieren archivos reales específicos

---

## Próximas fases recomendadas

1. **K.3**: Shell execution control (allowlist de comandos seguros)
2. **K.4**: Token optimization para el system prompt
3. **K.5**: Context window management para proyectos grandes
