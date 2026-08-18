# Local Development Setup

## Prerequisites

- Python 3.13+ (required by pyproject.toml)
- pip

## Quick Start (Windows / PowerShell)

From PowerShell, run everything from the project root:

```powershell
# 1. Create the venv and activate it
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies (editable mode)
python -m pip install -e ".[dev]"

# 3. Create .env (PowerShell Set-Content; values are placeholders, never real secrets)
@"
APP_ENV=development
AI_PROVIDER=deterministic
JWT_REQUIRED=false
JWT_SECRET=change-this-local-development-secret
DATABASE_URL=sqlite+aiosqlite:///./personal_ai_secretary.db
LOG_LEVEL=INFO
"@ | Set-Content -Path .env -Encoding UTF8

# 4. Initialize the database and run migrations (creates personal_ai_secretary.db)
python -m alembic upgrade head

# 5. Start the server
python -m uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000

# 6. Verify health (second terminal)
curl http://127.0.0.1:8000/api/v1/health/live
# -> {"status":"alive"}
```

> Bash/Git Bash: replace step 3 with `cp .env.example .env` then edit by hand, or use
> the `cat > .env << 'EOF' ... EOF` heredoc. The `.env` in this repo already exists
> for local development; do not reuse the placeholder `JWT_SECRET` in production.

## Running Tests

```bash
# Full test suite (358 tests)
python -m pytest tests/ -x -q

# With coverage (94%+)
python -m pytest tests/ --cov=personal_ai_secretary --cov-report=term-missing --cov-fail-under=94 -x -q

# Unit tests only
python -m pytest tests/unit/ -x -q

# Integration tests
python -m pytest tests/integration/ -x -q
```

## Type Checking & Linting

```bash
# Type checking (0 errors)
python -m mypy src/personal_ai_secretary --ignore-missing-imports

# Linting (0 errors)
python -m ruff check src/ tests/
```

## Database

```bash
# Run migrations (SQLite auto-applied)
alembic upgrade head

# Check current migration
alembic heads
# -> 0007_evidence_retention_and_stale_index
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | `development` or `production` |
| `AI_PROVIDER` | `deterministic` | `deterministic`, `local` (Ollama), or `remote` (NVIDIA) |
| `JWT_REQUIRED` | `true` | Set to `false` for local dev without auth |
| `JWT_SECRET` | `change-this-local-development-secret` | Required in production; must be overridden |
| `DATABASE_URL` | `sqlite+aiosqlite:///./personal_ai_secretary.db` | Database connection |
| `PERSISTENT_STORES` | `false` | Enable Postgres-backed stores |
| `DB_POOL_SIZE` | `5` | PostgreSQL connection pool size (ignored for SQLite) |
| `DB_MAX_OVERFLOW` | `10` | PostgreSQL max overflow (ignored for SQLite) |
| `OTEL_ENABLED` | `true` | Enable in-process tracing |
| `OTEL_EXPORTER_ENDPOINT` | `None` | OTLP/HTTP endpoint for span export |
| `LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR |
| `COMPLIANCE_ENABLED` | `true` | Enable compliance policy stage |
| `COMPLIANCE_RULES` | `prohibited_commands,credential_leakage` | Comma-separated rule IDs |
| `EVIDENCE_TTL_SECONDS` | `None` | Optional TTL for evidence sources |
| `AUDIT_RETENTION_SECONDS` | `2592000` | Audit retention window (default 30 days) |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Background sweep interval |
| `STALE_RUNNING_SECONDS` | `300` | Stale running recovery threshold |
| `PROVIDER_TIMEOUT_SECONDS` | `30.0` | AI provider HTTP timeout |

## API Endpoints

All endpoints require Bearer token unless `JWT_REQUIRED=false`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health/live` | Liveness check |
| GET | `/api/v1/health/ready` | Readiness check (DB + provider) |
| GET | `/api/v1/providers` | Active provider info |
| POST | `/api/v1/requests` | Create request |
| GET | `/api/v1/requests/{id}` | Get request status |
| POST | `/api/v1/requests/{id}/execute` | Execute request |
| POST | `/api/v1/sessions/{id}/messages` | Send message |
| GET | `/api/v1/sessions/{id}/messages` | Get session history |
| GET | `/api/v1/sessions/{id}` | Get session metadata |
| POST | `/api/v1/evidence` | Store evidence |
| GET | `/api/v1/evidence` | Search evidence |
| GET | `/api/v1/observability/audit` | Audit events |
| GET | `/api/v1/observability/metrics` | Metrics snapshot |
| GET | `/api/v1/metrics` | Prometheus metrics |
| GET | `/docs` | Swagger UI |

## Provider Modes

### Deterministic (default, no external services needed)
Returns fixed responses for testing. Always available.

## Smoke Test

With the server running and `JWT_REQUIRED=false` (local dev), verify a complete
message round-trip. Note the request body field is **`input`** (not `prompt`).

```powershell
# 1. Reuse a session id across calls (PowerShell)
$sid = [guid]::NewGuid().ToString()

# 2. Create a request
curl -s -X POST http://127.0.0.1:8000/api/v1/requests `
  -H "Content-Type: application/json" `
  -d ('{"input":"hello","session_id":"' + $sid + '"}')

# 3. Send a message and get a deterministic response
curl -s -X POST http://127.0.0.1:8000/api/v1/sessions/$sid/messages `
  -H "Content-Type: application/json" `
  -d '{"input":"remember my favorite color is blue"}'
# -> status "completed", assistant "DETERMINISTIC_RESPONSE: ..."

# 4. Read session metadata and history
curl -s http://127.0.0.1:8000/api/v1/sessions/$sid
curl -s http://127.0.0.1:8000/api/v1/sessions/$sid/messages

# 5. Observability
curl -s "http://127.0.0.1:8000/api/v1/observability/audit?limit=5"
curl -s http://127.0.0.1:8000/api/v1/observability/metrics
```

### Provider Modes

#### Deterministic (default, no external services needed)
Returns fixed responses for testing. Always available.

### Ollama (local, requires Ollama installed and a pulled model)
The provider mode value is **`local`** (not `ollama`).

```bash
# Terminal 1: start Ollama, then pull a model the app can use
ollama serve
ollama pull llama3.1:latest

# Terminal 2: run the API in local mode with an available model
AI_PROVIDER=local OLLAMA_MODEL=llama3.1:latest \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```
`GET /api/v1/providers` reports `"mode":"local"`. If it reports
`Ollama reachable but configured model is unavailable`, set `OLLAMA_MODEL` to a
model that is actually pulled (`ollama list`).

### NVIDIA (remote, requires NVIDIA_API_KEY)
The mode value is **`remote`** with `NVIDIA_API_KEY` set. Without a key the
provider reports `available: false` ("API key not configured") and `generate`
raises before any network call; with the key set, requests go to
`https://integrate.api.nvidia.com/v1/chat/completions`. The key is only ever sent
in the HTTP `Authorization` header and is never logged or returned in responses,
audit events, or spans.

```bash
AI_PROVIDER=remote NVIDIA_API_KEY=nvapi-xxx \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```
In production (`APP_ENV=production`) config validation rejects `AI_PROVIDER=remote`
without `NVIDIA_API_KEY`. Never commit a real key in `.env` or documentation.

## FASE 18 local automation

From PowerShell at the project root:

```powershell
.\scripts\setup_windows.ps1
.\scripts\verify_baseline.ps1
.\scripts\run_local.ps1
```

The project also declares `src` as the pytest Python path, so the test suite can be collected from the project root without relying on a previously installed editable package.

## Docker

### Build and Run

```powershell
docker build -t personal-ai-secretary .
docker run -p 8000:8000 personal-ai-secretary
```

The container runs migrations automatically on startup and exposes port 8000. A healthcheck is configured on `/api/v1/health/live`.

### Environment Variables for Docker

Pass environment variables via `-e` flags or a `.env` file:

```powershell
docker run -p 8000:8000 `
  -e APP_ENV=production `
  -e JWT_SECRET=your-strong-secret-here `
  -e AI_PROVIDER=deterministic `
  personal-ai-secretary
```

In production, you must set `JWT_SECRET` and `DATABASE_URL` to a PostgreSQL connection.

## Troubleshooting

### Tests fail with import errors

Ensure the package is installed in editable mode:
```powershell
pip install -e ".[dev]"
```

### Alembic migration fails

Check that your `DATABASE_URL` is valid. For SQLite, the path is relative to the project root:
```
DATABASE_URL=sqlite+aiosqlite:///./personal_ai_secretary.db
```

### Server won't start on port 8000

Another process may be using port 8000. Change the port:
```powershell
python -m uvicorn personal_ai_secretary.api.app:app --port 8001
```

### Ollama provider reports "model unavailable"

Set `OLLAMA_MODEL` to a model you have pulled:
```powershell
ollama list
# Set OLLAMA_MODEL to one of the listed model names
```

### NVIDIA provider raises "not configured"

Set `NVIDIA_API_KEY` in your `.env` or environment. The key is never logged.

## Security

- Never commit `.env` files (excluded via `.gitignore`)
- Never hardcode API keys or secrets in source code
- Audit redaction automatically sanitizes sensitive fields
- Production mode rejects default JWT secrets and SQLite databases
- All secrets are read from environment variables only
