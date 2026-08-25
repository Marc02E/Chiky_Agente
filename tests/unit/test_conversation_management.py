"""Tests for Phase G conversation management endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from personal_ai_secretary.api.app import app
from personal_ai_secretary.domain.models import Base, SessionRecord
from personal_ai_secretary.ui.routes import list_sessions_with_titles


def _make_client() -> TestClient:
    return TestClient(app)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


class TestSessionRename:
    def test_rename_existing_session(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Hello"},
                headers={"Idempotency-Key": f"rename-test-{sid}"},
            )
            resp = client.patch(
                f"/api/v1/ui/sessions/{sid}",
                json={"title": "My custom title"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "My custom title"
            assert data["session_id"] == sid

    def test_rename_updates_list(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Hi there"},
                headers={"Idempotency-Key": f"rename-list-{sid}"},
            )
            client.patch(
                f"/api/v1/ui/sessions/{sid}",
                json={"title": "Updated title"},
            )
            sessions_resp = client.get("/api/v1/ui/sessions")
            sessions = sessions_resp.json()["sessions"]
            found = next((s for s in sessions if s["session_id"] == sid), None)
            assert found is not None
            assert found["title"] == "Updated title"

    def test_rename_nonexistent_session_returns_404(self) -> None:
        with _make_client() as client:
            fake_id = str(uuid4())
            resp = client.patch(
                f"/api/v1/ui/sessions/{fake_id}",
                json={"title": "Ghost title"},
            )
            assert resp.status_code == 404

    def test_rename_empty_title_returns_422(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Test"},
                headers={"Idempotency-Key": f"empty-title-{sid}"},
            )
            resp = client.patch(
                f"/api/v1/ui/sessions/{sid}",
                json={"title": ""},
            )
            assert resp.status_code == 422

    def test_rename_title_too_long_returns_422(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Test"},
                headers={"Idempotency-Key": f"long-title-{sid}"},
            )
            resp = client.patch(
                f"/api/v1/ui/sessions/{sid}",
                json={"title": "x" * 201},
            )
            assert resp.status_code == 422


class TestSessionDelete:
    def test_delete_existing_session(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "To be deleted"},
                headers={"Idempotency-Key": f"del-test-{sid}"},
            )
            resp = client.delete(f"/api/v1/ui/sessions/{sid}")
            assert resp.status_code == 204

    def test_delete_removes_from_list(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Goodbye"},
                headers={"Idempotency-Key": f"del-list-{sid}"},
            )
            client.delete(f"/api/v1/ui/sessions/{sid}")
            sessions_resp = client.get("/api/v1/ui/sessions")
            sessions = sessions_resp.json()["sessions"]
            assert not any(s["session_id"] == sid for s in sessions)

    def test_delete_nonexistent_returns_404(self) -> None:
        with _make_client() as client:
            fake_id = str(uuid4())
            resp = client.delete(f"/api/v1/ui/sessions/{fake_id}")
            assert resp.status_code == 404

    def test_deleted_session_messages_gone(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Message to delete"},
                headers={"Idempotency-Key": f"del-msg-{sid}"},
            )
            client.delete(f"/api/v1/ui/sessions/{sid}")
            resp = client.get(f"/api/v1/sessions/{sid}/messages")
            assert resp.status_code == 404


class TestSessionSearch:
    def test_search_filters_by_title(self) -> None:
        with _make_client() as client:
            sid1 = str(uuid4())
            sid2 = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid1}/messages",
                json={"input": "Weather forecast today"},
                headers={"Idempotency-Key": f"search-a-{sid1}"},
            )
            client.post(
                f"/api/v1/sessions/{sid2}/messages",
                json={"input": "Recipe for pasta"},
                headers={"Idempotency-Key": f"search-b-{sid2}"},
            )
            resp = client.get("/api/v1/ui/sessions?search=Weather")
            data = resp.json()["sessions"]
            ids = [s["session_id"] for s in data]
            assert sid1 in ids
            assert sid2 not in ids

    def test_search_empty_returns_all(self) -> None:
        with _make_client() as client:
            resp = client.get("/api/v1/ui/sessions?search=")
            assert resp.status_code == 200

    def test_search_matches_renamed_title(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Original title"},
                headers={"Idempotency-Key": f"search-rename-{sid}"},
            )
            client.patch(
                f"/api/v1/ui/sessions/{sid}",
                json={"title": "Quarterly planning"},
            )
            resp = client.get("/api/v1/ui/sessions?search=Quarterly")
            sessions = resp.json()["sessions"]
            assert any(s["session_id"] == sid and s["title"] == "Quarterly planning" for s in sessions)


class TestSessionUpdatedAt:
    def test_sessions_have_updated_at(self) -> None:
        with _make_client() as client:
            sid = str(uuid4())
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "Check updated_at"},
                headers={"Idempotency-Key": f"upd-at-{sid}"},
            )
            resp = client.get("/api/v1/ui/sessions")
            sessions = resp.json()["sessions"]
            found = next((s for s in sessions if s["session_id"] == sid), None)
            assert found is not None
            assert "updated_at" in found


@pytest.mark.asyncio
async def test_list_sessions_with_titles_uses_fallback_for_empty_session(session: AsyncSession) -> None:
    record = SessionRecord(
        session_id=uuid4(),
        user_id="development-user",
        title=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        request_count=0,
    )
    session.add(record)
    await session.commit()

    response = await list_sessions_with_titles(
        limit=50,
        search=None,
        claims={},
        db=session,
    )

    assert response.sessions[0].title == "New conversation"
