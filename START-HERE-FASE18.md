# personal_ai_secretary — RELEASE 1.0

Governed personal AI secretary platform with multi-provider AI support, conversation memory, compliance governance, full observability, and web-based user interface.

## Quick Start (Windows / PowerShell)

```powershell
cd <project-root>
.\scripts\setup_windows.ps1
.\scripts\launch.bat
```

## What Each Script Does

| Script | Purpose |
|--------|---------|
| `setup_windows.ps1` | Creates venv, installs deps, creates .env, runs migrations |
| `launch.bat` | **Launches the application** (starts server + opens browser) |
| `launch.py` | Python launcher (called by launch.bat) |
| `verify_baseline.ps1` | Runs pytest+coverage, mypy, ruff, alembic heads |
| `run_local.ps1` | Runs migrations and starts uvicorn (developer shortcut) |

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

## Release 1.0 + UI Status

**RELEASE 1.0 + UI = READY**

All release gates verified:
- Reproducible Windows setup
- Baseline verification (420 tests, 96.15% coverage, mypy 0, ruff 0, Alembic 0007)
- Application startup and health
- API functional verification (20 endpoint checks)
- Web UI: Chiky agente (dark theme, conversation interface)
- Application launcher (launch.bat → server + browser)
- Real-world smoke tests
- Security and secrets audit
- Docker (statically verified; runtime requires Docker Desktop)
- Observability and operations
- Provider verification (deterministic + Ollama verified)
- Multi-turn conversations with context injection
- Session/request listing APIs
- Cross-user isolation
- Production guards (JWT, SQLite, NVIDIA key)
- Wheel + sdist build validated
- CI/CD workflow validated

## Fases 2–21 = CLOSED
## Fase UI = CLOSED
## Release 1.0 + UI = READY
