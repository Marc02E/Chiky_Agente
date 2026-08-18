import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from personal_ai_secretary.application.service import RequestService
from personal_ai_secretary.domain.contracts import (
    ProviderInfo,
    ProviderResponse,
    RequestCreate,
    RequestEnvelope,
)
from personal_ai_secretary.domain.models import Base, RequestRecord


class _ConcurrentProvider:
    name = "concurrent"

    def __init__(self) -> None:
        self.calls = 0

    async def health(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, mode="test", available=True, is_ai=False, detail=""
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        self.calls += 1
        await asyncio.sleep(0.05)
        return ProviderResponse(text="processed", provider=self.name)


@pytest.mark.asyncio
async def test_concurrent_execute_runs_workflow_exactly_once(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrency.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = _ConcurrentProvider()

    async with factory() as session:
        service = RequestService(session, provider)
        accepted = await service.create(
            RequestCreate(input="hello"), "user-1", "corr-concurrent", None
        )

    async def run() -> str:
        async with factory() as session:
            service = RequestService(session, provider)
            result = await service.execute(accepted.request_id, "user-1")
            return result.status if result is not None else "none"

    statuses = await asyncio.gather(run(), run(), run())

    assert provider.calls == 1
    assert "completed" in statuses
    assert statuses.count("completed") >= 1

    async with factory() as session:
        final = await session.get(RequestRecord, accepted.request_id)
        assert final is not None
        assert final.status == "completed"
        assert final.result == "processed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_execute_is_idempotent_when_already_completed(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'idempotent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = _ConcurrentProvider()

    async with factory() as session:
        service = RequestService(session, provider)
        accepted = await service.create(
            RequestCreate(input="hello"), "user-1", "corr-idem", None
        )
        first = await service.execute(accepted.request_id, "user-1")

    async with factory() as session:
        service = RequestService(session, provider)
        second = await service.execute(accepted.request_id, "user-1")

    assert first is not None and first.status == "completed"
    assert second is not None and second.status == "completed"
    assert provider.calls == 1
    await engine.dispose()