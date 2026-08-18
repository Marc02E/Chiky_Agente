from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from personal_ai_secretary.api.app import app
from personal_ai_secretary.domain.models import AuditEventRecord, MemoryItemRecord
from personal_ai_secretary.infrastructure import database
from personal_ai_secretary.shared.config import get_settings


async def test_persistent_backend_persists_audit_and_memory(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "persistent.db"
    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(settings, "persistent_stores", True)
    database._engine = None
    database._session_factory = None

    with TestClient(app) as client:
        session_id = uuid4()
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "persist-1"},
            json={"input": "hello"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"

        audit = client.get("/api/v1/observability/audit").json()
        assert len(audit) >= 8
        assert any(event["event_type"] == "request_received" for event in audit)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        memory_rows = list((await session.scalars(select(MemoryItemRecord))).all())
        audit_rows = list((await session.scalars(select(AuditEventRecord))).all())
    await engine.dispose()

    assert len(memory_rows) == 1
    assert memory_rows[0].user_id == "development-user"
    assert "hello" in memory_rows[0].content
    assert len(audit_rows) >= 8
    assert all(row.details for row in audit_rows)


async def test_persistent_backend_survives_app_restart(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "restart.db"
    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(settings, "persistent_stores", True)
    database._engine = None
    database._session_factory = None

    request_id = None
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "restart-1"},
            json={"input": "persisted hello"},
        )
        assert response.json()["status"] == "completed"
        request_id = response.json()["request_id"]

    with TestClient(app) as client:
        status = client.get(f"/api/v1/requests/{request_id}").json()
        assert status["status"] == "completed"
        assert status["result"] == "DETERMINISTIC_RESPONSE: persisted hello"

        audit = client.get("/api/v1/observability/audit").json()
        assert any(event["event_type"] == "request_received" for event in audit)

        history = client.get(f"/api/v1/sessions/{session_id}/messages").json()
        assert len(history["messages"]) == 2

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        memory_rows = list((await session.scalars(select(MemoryItemRecord))).all())
    await engine.dispose()
    assert len(memory_rows) == 1
    assert "persisted hello" in memory_rows[0].content