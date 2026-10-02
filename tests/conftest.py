import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AI_PROVIDER", "deterministic")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-32-bytes")
os.environ.setdefault("JWT_REQUIRED", "false")


@pytest.fixture(autouse=True)
def isolated_filesystem_roots(tmp_path) -> Iterator[None]:
    """Permit each test's sandbox regardless of the runner's home directory."""
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS.append(str(tmp_path))
    try:
        yield
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.fixture(autouse=True)
def isolated_postgres_state(monkeypatch) -> None:
    """PostgreSQL persists across app lifespans; tests must not share rows."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        return

    import psycopg
    from psycopg import sql

    from personal_ai_secretary.infrastructure import database

    # Some unit tests initialize the module outside an app lifespan. Never
    # reuse their engine/session factory in a later TestClient event loop.
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)

    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    sync_url = sync_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(sync_url, autocommit=True) as connection:
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        ).fetchall()
        if tables:
            connection.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier("public", row[0]) for row in tables)
                )
            )


@pytest.fixture
def manual_routing_manager(monkeypatch):
    """Pin the app to MANUAL routing for this test.

    FASE AB.4/AB.5: under AUTOMATIC routing a provider failure triggers a
    *real* model fallback. Error-contract unit tests (provider raised ->
    user sees a clear error) must run in MANUAL mode, where dead providers
    are surfaced instead of silently switched. This isolates those tests
    from an initialized global ModelManager singleton that earlier
    integration tests may have created.
    """
    from personal_ai_secretary.providers.model_manager import ModelManager

    manager = ModelManager()
    manager.set_routing_mode("manual")
    monkeypatch.setattr(
        "personal_ai_secretary.providers.factory.get_model_manager",
        lambda: manager,
    )
    return manager
