# personal_ai_secretary — RELEASE CANDIDATE

This archive is a clean single-root development package prepared from the verified restoration backup.

## Quick Start (Windows / PowerShell)

```powershell
cd <project-root>
.\scripts\setup_windows.ps1
.\scripts\verify_baseline.ps1
.\scripts\run_local.ps1
```

## What Each Script Does

| Script | Purpose |
|--------|---------|
| `setup_windows.ps1` | Creates venv, installs deps, creates .env, runs migrations |
| `verify_baseline.ps1` | Runs pytest+coverage, mypy, ruff, alembic heads |
| `run_local.ps1` | Runs migrations and starts uvicorn on port 8000 |

## Manual Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# Create .env (see .env.example for reference)
python -m alembic upgrade head
python -m uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

## Verify Health

```powershell
curl http://127.0.0.1:8000/api/v1/health/live
curl http://127.0.0.1:8000/api/v1/health/ready
```

## Smoke Test (with server running, JWT_REQUIRED=false)

```powershell
$sid = [guid]::NewGuid().ToString()
curl -s -X POST http://127.0.0.1:8000/api/v1/sessions/$sid/messages `
  -H "Content-Type: application/json" `
  -d '{"input":"hello"}'
```

## Docker

```powershell
docker build -t personal-ai-secretary .
docker run -p 8000:8000 personal-ai-secretary
```

## FASE 21 Status

**FASE 21 = CLOSED — RELEASE READY**

All release gates verified:
- Reproducible Windows setup
- Baseline verification (386 tests, 96.27% coverage, mypy 0, ruff 0, Alembic 0007)
- Application startup and health
- API functional verification
- Real-world smoke tests
- Security and secrets audit
- Docker (statically verified; runtime requires Docker Desktop)
- Observability and operations
- Provider verification (deterministic + Ollama verified)
- Multi-turn conversations with context injection
- Session/request listing APIs
- Cross-user isolation
- Production guards (JWT, SQLite, NVIDIA key)
