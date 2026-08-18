from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from personal_ai_secretary.shared.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def test_alembic_upgrade_applies_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "migrated.db"
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{db_path}")

    command.upgrade(_config(tmp_path), "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"sessions", "requests", "memory_items", "audit_events", "alembic_version"} <= tables
    assert "metric_records" in tables
    assert "evidence_sources" in tables


def test_alembic_downgrade_removes_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "migrated.db"
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{db_path}")

    command.upgrade(_config(tmp_path), "head")
    command.downgrade(_config(tmp_path), "base")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "requests" not in tables
    assert "sessions" not in tables
    assert "memory_items" not in tables
    assert "audit_events" not in tables
    assert "metric_records" not in tables
    assert "evidence_sources" not in tables


def test_alembic_downgrade_removes_only_phase9_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "migrated.db"
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{db_path}")

    command.upgrade(_config(tmp_path), "head")
    command.downgrade(_config(tmp_path), "0001_initial")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"sessions", "requests", "alembic_version"} <= tables
    assert "memory_items" not in tables
    assert "audit_events" not in tables
    assert "evidence_sources" not in tables
