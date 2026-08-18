from pathlib import Path

import pytest

from personal_ai_secretary.infrastructure import database


@pytest.mark.asyncio
async def test_database_lifecycle_with_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "phase1.db"
    settings = database.get_settings()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    await database.close_database()

    await database.init_database()
    factory = database.get_session_factory()
    async with factory() as session:
        assert session.is_active

    await database.close_database()
    assert database._engine is None
    assert database._session_factory is None
