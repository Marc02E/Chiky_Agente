# CI repair validation

## Causes and corrections

- Declare `sqlalchemy[asyncio]` so clean Python 3.13 installations include
  `greenlet`, including the production Docker image.
- Docker smoke checks retry transient startup failures and always clean up;
  failed runs retain container logs and inspection output.
- Run offline CI with `-m "not slow"`. Live Ollama/OpenCode tests remain
  available separately. Collection no longer enables live processes globally.
- Isolate temporary filesystem roots and PostgreSQL data between tests.
  Test-environment PostgreSQL connections use NullPool because TestClient
  lifespans run on different event loops; production retains configured pooling.
- Publish metric snapshots using atomic upsert instead of repeated inserts
  violating the unique worker/metric constraint.
- Disable FastAPI's duplicate automatic telemetry; retain application-owned
  sanitized observability. Extract incoming trace headers from a clean context.
- Reject Windows drive/UNC paths on POSIX and remove platform-dependent test
  assumptions. Keep the existing Windows Job Object fallback Linux-type-safe.
- Add offline regression tests rather than reducing the 94% coverage gate.

## Verified locally

- Linux / Python 3.13 / SQLite: 2,279 passed, 2 skipped, 40 live tests deselected;
  96.64% statement coverage.
- Linux / Python 3.13 / PostgreSQL 16: 2,279 passed, 2 skipped, 40 live tests
  deselected; 96.74% statement coverage.
- Ruff and mypy pass on Windows and Linux (84 source files).
- Final production image builds, applies Alembic migrations and responds
  `{"status":"alive"}` to the Docker smoke health check.
- Repeated metric snapshot regression and filesystem/process regressions pass.

Real model inference requires the corresponding external providers and is not
certified by the offline CI suite. Existing non-fatal test warnings remain.