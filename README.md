# Personal AI Secretary — Chiky agente

A governed personal AI secretary platform with multi-provider AI support, conversation memory, compliance governance, full observability, and a web-based user interface.

## Quick Start (Windows)

```powershell
# 1. Setup (creates venv, installs deps, creates .env, runs migrations)
.\scripts\setup_windows.ps1

# 2. Launch the application (starts server + opens browser)
.\scripts\launch.bat
```

The application opens in your browser at `http://127.0.0.1:8000`. Start chatting immediately.

## Features

## Manual Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .\.venv\Scripts\Activate.ps1  # Windows

# Install dependencies
pip install -e ".[dev]"

# Configure environment (copy and edit .env.example)
cp .env.example .env

# Run migrations
python -m alembic upgrade head

# Start server
python -m uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

## Verify Health

```bash
curl http://127.0.0.1:8000/api/v1/health/live
# -> {"status":"alive"}

curl http://127.0.0.1:8000/api/v1/health/ready
# -> {"status":"ready","provider":{...},"database":"ok"}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web UI (Chiky agente) |
| GET | `/api/v1/health/live` | Liveness check |
| GET | `/api/v1/health/ready` | Readiness check (DB + provider) |
| GET | `/api/v1/providers` | Active provider info |
| POST | `/api/v1/requests` | Create request |
| GET | `/api/v1/requests/{id}` | Get request status |
| POST | `/api/v1/requests/{id}/execute` | Execute request |
| GET | `/api/v1/requests` | List user requests |
| POST | `/api/v1/sessions/{id}/messages` | Send message |
| GET | `/api/v1/sessions/{id}/messages` | Get session history |
| GET | `/api/v1/sessions/{id}` | Get session metadata |
| GET | `/api/v1/sessions` | List user sessions |
| GET | `/api/v1/ui/sessions` | List sessions with titles (for UI), supports `?search=` |
| PATCH | `/api/v1/ui/sessions/{id}` | Rename a conversation |
| DELETE | `/api/v1/ui/sessions/{id}` | Delete a conversation and its messages |
| POST | `/api/v1/evidence` | Store evidence |
| GET | `/api/v1/evidence` | Search evidence |
| GET | `/api/v1/observability/audit` | Audit events |
| GET | `/api/v1/observability/metrics` | Metrics snapshot |
| GET | `/api/v1/metrics` | Prometheus metrics |
| GET | `/docs` | Swagger UI (developer) |

All endpoints require a valid Bearer token unless `JWT_REQUIRED=false`.

## Provider Modes

### Deterministic (default)

Returns fixed responses for testing. No external services needed.

```bash
python -m uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

### Ollama (local)

Requires [Ollama](https://ollama.com) installed with a pulled model.

```bash
# Start Ollama and pull a model
ollama serve
ollama pull llama3.1:latest

# Run with local provider
AI_PROVIDER=local OLLAMA_MODEL=llama3.1:latest \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

### NVIDIA (remote)

Requires an `NVIDIA_API_KEY`. In production, the application refuses to start without it.

```bash
AI_PROVIDER=remote NVIDIA_API_KEY=nvapi-xxx \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

## Docker

```bash
# Build
docker build -t personal-ai-secretary .

# Run (development)
docker run -p 8000:8000 \
  -e JWT_REQUIRED=false \
  -e AI_PROVIDER=deterministic \
  personal-ai-secretary

# Run (production)
docker run -p 8000:8000 \
  -e APP_ENV=production \
  -e JWT_SECRET=your-strong-secret \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@host/db \
  personal-ai-secretary
```

The container runs migrations automatically on startup and includes a healthcheck on `/api/v1/health/live`.

## Testing

```bash
# Full test suite (612 tests)
python -m pytest tests/ -q

# With coverage (94%+ threshold)
python -m pytest tests/ --cov=personal_ai_secretary --cov-fail-under=94 -q

# Type checking
python -m mypy src/personal_ai_secretary --ignore-missing-imports

# Linting
python -m ruff check src/ tests/
```

## Configuration

See `.env.example` for all environment variables. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | `development`, `test`, or `production` |
| `AI_PROVIDER` | `deterministic` | `deterministic`, `local`, or `remote` |
| `JWT_SECRET` | dev default | **Required in production** |
| `JWT_REQUIRED` | `true` | Set `false` for local dev without auth |
| `DATABASE_URL` | SQLite | PostgreSQL connection for production |
| `PERSISTENT_STORES` | `false` | Enable PostgreSQL-backed stores |

## Production Guards

When `APP_ENV=production`, the application refuses to start if:

- `JWT_SECRET` is the default development value
- `DATABASE_URL` points to SQLite
- `AI_PROVIDER=remote` without `NVIDIA_API_KEY`

## Project Structure

```
src/personal_ai_secretary/
├── api/              # FastAPI application and endpoints
├── application/      # Request service and risk classification
├── agents/           # Governed agent roles (Planner, Research, Execution, Reviewer, Compliance)
├── compliance/       # Policy enforcement rules
├── domain/           # Contracts, models, and business logic
├── evaluation/       # Release gate evaluator
├── infrastructure/   # Database, memory, RAG, observability wiring
├── memory/           # Memory store and policy
├── observability/    # Audit, metrics, tracing
├── providers/        # AI providers (Deterministic, Ollama, NVIDIA)
├── rag/              # Evidence retrieval and governance
├── shared/           # Config, auth, telemetry
├── tools/            # Tool registry and built-in tools
├── ui/               # Web interface (HTML/CSS/JS SPA)
└── workflow/         # Governed workflow engine

scripts/
├── launch.py         # Application launcher (starts server + opens browser)
├── launch.bat        # Windows double-click launcher
├── run_local.ps1     # Start server directly (developer)
├── setup_windows.ps1 # Initial setup (developer)
└── verify_baseline.ps1 # Quality gate verification (developer)
```

## Limitations

- **Docker**: Requires Docker Desktop or Docker Engine. Smoke-tested in CI.
- **PostgreSQL**: Required for production persistence. SQLite is used for development/testing.
- **NVIDIA**: Requires a valid API key from [build.nvidia.com](https://build.nvidia.com).

## Security

- Never commit `.env` files (excluded via `.gitignore`)
- Never hardcode API keys or secrets in source code
- Audit redaction automatically sanitizes sensitive fields
- Production mode rejects default JWT secrets and SQLite databases
- All secrets are read from environment variables only

## License

No license file is provided. This is a private repository. Contact the author for usage rights.
