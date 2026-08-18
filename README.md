# Personal AI Secretary

A governed personal AI secretary platform with multi-provider support, conversation memory, compliance governance, and full observability.

## Features

- **Multi-provider AI**: Deterministic (test), Ollama (local), NVIDIA (remote)
- **Governed workflow**: Planner → Research → Execution → Review → Compliance
- **Conversation memory**: Multi-turn conversations with context injection
- **Session management**: Create, list, and track sessions with history
- **Compliance**: Automated policy enforcement (prohibited commands, credential leakage)
- **Observability**: Audit trail, metrics, distributed tracing (OpenTelemetry)
- **Security**: JWT authentication, user isolation, production guards
- **Persistence**: SQLite (dev) or PostgreSQL (production)

## Quick Start (Windows / PowerShell)

```powershell
# 1. Setup (creates venv, installs deps, creates .env, runs migrations)
.\scripts\setup_windows.ps1

# 2. Verify baseline (tests, coverage, mypy, ruff, alembic)
.\scripts\verify_baseline.ps1

# 3. Start server
.\scripts\run_local.ps1
```

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
curl http://127.0.0.1:8000/api/v1/health/ready
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
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
| POST | `/api/v1/evidence` | Store evidence |
| GET | `/api/v1/evidence` | Search evidence |
| GET | `/api/v1/observability/audit` | Audit events |
| GET | `/api/v1/observability/metrics` | Metrics snapshot |
| GET | `/api/v1/metrics` | Prometheus metrics |
| GET | `/docs` | Swagger UI |

## Provider Modes

### Deterministic (default)
Returns fixed responses for testing. No external services needed.

### Ollama (local)
```bash
AI_PROVIDER=local OLLAMA_MODEL=llama3.1:latest \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

### NVIDIA (remote)
```bash
AI_PROVIDER=remote NVIDIA_API_KEY=nvapi-xxx \
  uvicorn personal_ai_secretary.api.app:app --host 127.0.0.1 --port 8000
```

## Docker

```bash
docker build -t personal-ai-secretary .
docker run -p 8000:8000 personal-ai-secretary
```

For production:
```bash
docker run -p 8000:8000 \
  -e APP_ENV=production \
  -e JWT_SECRET=your-strong-secret \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@host/db \
  personal-ai-secretary
```

## Testing

```bash
# Full test suite (386 tests)
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
| `JWT_REQUIRED` | `true` | Set `false` for local dev |
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
└── workflow/         # Governed workflow engine
```

## License

Private repository.
