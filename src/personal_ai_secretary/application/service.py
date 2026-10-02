import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_ai_secretary.application.risk import classify_risk
from personal_ai_secretary.domain.contracts import (
    ConversationHistory,
    ConversationMessage,
    RequestAccepted,
    RequestCreate,
    RequestStatus,
    SendMessageResponse,
    SessionStatus,
)
from personal_ai_secretary.domain.models import RequestRecord, SessionRecord
from personal_ai_secretary.evaluation.runtime import ReleaseGateEvaluator
from personal_ai_secretary.memory.service import (
    MemoryClass,
    MemoryItem,
    MemoryPolicy,
    MemoryStore,
)
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.rag.service import Retriever
from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.shared.telemetry import get_correlation_id
from personal_ai_secretary.tools.registry import ToolRegistry
from personal_ai_secretary.workflow.engine import GovernedWorkflow, WorkflowResult

logger = logging.getLogger("personal_ai_secretary.application")

_STALE_RUNNING_RESULT = (
    "Workflow execution was interrupted; request recovered from a stale running state."
)

_MANUAL_UNAVAILABLE_MESSAGE = (
    "The selected provider is unavailable in MANUAL routing mode; "
    "no automatic fallback is allowed. Enable Automatic routing or select "
    "an available provider."
)


class RequestService:
    def __init__(
        self,
        session: AsyncSession,
        provider: AIProvider | None,
        memory: MemoryStore | None = None,
        retriever: Retriever | None = None,
        tools: ToolRegistry | None = None,
        evaluator: ReleaseGateEvaluator | None = None,
        observability: Observability | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.memory = memory
        self.retriever = retriever
        self.tools = tools
        self.evaluator = evaluator
        self.observability = observability
        self._last_fallback: dict[str, Any] | None = None

    async def create(
        self,
        payload: RequestCreate,
        user_id: str,
        correlation_id: str,
        idempotency_key: str | None,
    ) -> RequestAccepted:
        if idempotency_key:
            existing = await self._find_by_idempotency_key(idempotency_key, user_id)
            if existing:
                if payload.session_id is not None and existing.session_id != payload.session_id:
                    raise ValueError(
                        "Idempotency-Key is already associated with another session"
                    ) from None
                return RequestAccepted(
                    request_id=existing.request_id,
                    correlation_id=existing.correlation_id,
                )

        session_id = payload.session_id or uuid4()
        session_record = await self.session.get(SessionRecord, session_id)
        if session_record is None:
            session_record = SessionRecord(
                session_id=session_id,
                user_id=user_id,
                request_count=0,
            )
            self.session.add(session_record)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                session_record = await self.session.get(SessionRecord, session_id)
                if session_record is None:
                    raise RuntimeError(
                        "Session could not be created"
                    ) from None
        if session_record.user_id != user_id:
            raise PermissionError("Session belongs to another user")

        request_id = uuid4()
        record = RequestRecord(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            input=payload.input,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            status="accepted",
        )
        self.session.add(record)
        if (session_record.request_count or 0) == 0 and not session_record.title:
            session_record.title = payload.input[:80]
        session_record.request_count = (session_record.request_count or 0) + 1
        session_record.updated_at = datetime.now(UTC)

        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if idempotency_key:
                existing = await self._find_by_idempotency_key(idempotency_key, user_id)
                if existing:
                    if payload.session_id is not None and existing.session_id != payload.session_id:
                        raise ValueError(
                            "Idempotency-Key is already associated with another session"
                        ) from None
                    return RequestAccepted(
                        request_id=existing.request_id,
                        correlation_id=existing.correlation_id,
                    )
            raise

        return RequestAccepted(request_id=request_id, correlation_id=correlation_id)

    async def _find_by_idempotency_key(
        self,
        idempotency_key: str,
        user_id: str,
    ) -> RequestRecord | None:
        result = await self.session.scalars(
            select(RequestRecord).where(
                RequestRecord.idempotency_key == idempotency_key,
                RequestRecord.user_id == user_id,
            )
        )
        return result.first()

    async def get(self, request_id: UUID, user_id: str | None = None) -> RequestStatus | None:
        query = select(RequestRecord).where(RequestRecord.request_id == request_id)
        if user_id is not None:
            query = query.where(RequestRecord.user_id == user_id)
        record = await self.session.scalar(query)
        if record is None:
            return None
        return RequestStatus(
            request_id=record.request_id,
            status=record.status,
            result=record.result,
            correlation_id=record.correlation_id,
        )

    async def execute(
        self,
        request_id: UUID,
        user_id: str | None = None,
        approval_granted: bool = False,
        image_attachments: list[str] | None = None,
    ) -> RequestStatus | None:
        transition = (
            update(RequestRecord)
            .where(RequestRecord.request_id == request_id)
            .where(
                RequestRecord.status.in_(
                    ("accepted", "blocked", "rejected", "failed")
                )
            )
            .values(status="running", updated_at=datetime.now(UTC))
        )
        if user_id is not None:
            transition = transition.where(RequestRecord.user_id == user_id)
        transition_result = cast(
            CursorResult[Any], await self.session.execute(transition)
        )
        await self.session.commit()
        if transition_result.rowcount == 0:
            existing = await self.get(request_id, user_id)
            if existing is not None and existing.status == "running":
                if await self._recover_stale_request(request_id):
                    await self.session.commit()
                    return await self.get(request_id, user_id)
            return existing

        query = select(RequestRecord).where(RequestRecord.request_id == request_id)
        if user_id is not None:
            query = query.where(RequestRecord.user_id == user_id)
        record = await self.session.scalar(query)
        if record is None:
            return None

        observation = self.observability
        if observation is not None:
            observation.inc("requests_total")
            await observation.emit(
                stage="request_received",
                request_id=record.request_id,
                user_id=record.user_id,
                correlation_id=record.correlation_id,
                session_id=record.session_id,
                outcome="running",
            )
        workflow = GovernedWorkflow(
            provider=self.provider,
            retriever=self.retriever,
            memory=self.memory,
            tools=self.tools,
            evaluator=self.evaluator,
            observability=observation,
        )
        session_history = await self._fetch_session_history(record.session_id, record.user_id)
        if self.provider is None:
            # FASE RELEASE: MANUAL mode with an unavailable provider. Fail
            # explicitly with provenance instead of silently executing another
            # provider. _service() pre-set self._last_fallback with the
            # "unavailable" provenance for this request; do not overwrite it.
            record.status = "failed"
            record.result = _MANUAL_UNAVAILABLE_MESSAGE
            if observation is not None:
                observation.inc("requests_failed")
                await observation.emit(
                    stage="request",
                    request_id=record.request_id,
                    user_id=record.user_id,
                    correlation_id=record.correlation_id,
                    session_id=record.session_id,
                    outcome="failed",
                    error=_MANUAL_UNAVAILABLE_MESSAGE,
                )
            record.updated_at = datetime.now(UTC)
            await self.session.commit()
            return await self.get(request_id, user_id)
        result: WorkflowResult | None = None
        # FASE AB.6: image attachments travel to the vision provider directly.
        _context: dict[str, object] = {"approval_granted": approval_granted}
        if image_attachments:
            _context["images"] = list(image_attachments)
        try:
            result = await workflow.run(
                request_id=record.request_id,
                session_id=record.session_id,
                user_id=record.user_id,
                text=record.input,
                correlation_id=record.correlation_id,
                risk_level=classify_risk(record.input),
                context=_context,
                session_history=session_history,
            )
        except Exception as exc:
            logger.exception("Governed workflow execution failed for request %s", request_id)
            record.status = "failed"
            record.result = (
                "I encountered an unexpected error while processing your request. "
                "Please try again."
            )
            if observation is not None:
                observation.inc("requests_failed")
                await observation.emit(
                    stage="request",
                    request_id=record.request_id,
                    user_id=record.user_id,
                    correlation_id=record.correlation_id,
                    session_id=record.session_id,
                    outcome="failed",
                    error=exc,
                )
        if result is not None:
            self._last_fallback = result.fallback
            record.status = result.status
            if result.status == "completed":
                record.result = result.response
            elif result.status == "rejected":
                record.result = result.rejected_reason
            else:
                record.result = result.blocked_reason
            if observation is not None:
                metric = {
                    "completed": "requests_completed",
                    "blocked": "requests_blocked",
                    "rejected": "requests_rejected",
                }.get(result.status)
                if metric is not None:
                    observation.inc(metric)
                await observation.emit(
                    stage="request",
                    request_id=record.request_id,
                    user_id=record.user_id,
                    correlation_id=record.correlation_id,
                    session_id=record.session_id,
                    outcome=result.status,
                    details={"response": result.response},
                )
        if record.status == "completed" and record.result is not None:
            try:
                await self._remember(record.user_id, record.input, record.result)
            except Exception as exc:
                logger.exception(
                    "Memory persistence failed for request %s; request remains %s",
                    request_id,
                    record.status,
                )
                if observation is not None:
                    await observation.emit(
                        stage="memory",
                        request_id=record.request_id,
                        user_id=record.user_id,
                        correlation_id=record.correlation_id,
                        session_id=record.session_id,
                        outcome="error",
                        error=exc,
                    )
        record.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get(request_id, user_id)

    async def _remember(self, user_id: str, user_input: str, response: str) -> None:
        if self.memory is None:
            return
        await self.memory.add(
            MemoryItem(
                memory_id=str(uuid4()),
                user_id=user_id,
                content=f"user: {user_input}\nassistant: {response}",
                memory_class=MemoryClass.SESSION,
                created_at=datetime.now(UTC),
                expires_at=MemoryPolicy().expiration(MemoryClass.SESSION),
            )
        )

    async def recover_stale_running(self, older_than_seconds: int | None = None) -> int:
        threshold = (
            older_than_seconds
            if older_than_seconds is not None
            else get_settings().stale_running_seconds
        )
        cutoff = datetime.now(UTC) - timedelta(seconds=threshold)
        running = await self.session.scalars(
            select(RequestRecord).where(RequestRecord.status == "running")
        )
        recovered = 0
        for record in running.all():
            if not await self._is_stale_running(record, cutoff):
                continue
            transition_result = await self._mark_stale_failed(record.request_id)
            if transition_result == 1:
                recovered += 1
        await self.session.commit()
        return recovered

    async def _recover_stale_request(self, request_id: UUID) -> bool:
        cutoff = datetime.now(UTC) - timedelta(
            seconds=get_settings().stale_running_seconds
        )
        record = await self.session.get(RequestRecord, request_id)
        if record is None or not await self._is_stale_running(record, cutoff):
            return False
        return await self._mark_stale_failed(request_id) == 1

    async def _is_stale_running(
        self, record: RequestRecord, cutoff: datetime
    ) -> bool:
        if record.status != "running":
            return False
        updated = record.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return updated < cutoff

    async def _mark_stale_failed(self, request_id: UUID) -> int:
        transition_result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(RequestRecord)
                .where(RequestRecord.request_id == request_id)
                .where(RequestRecord.status == "running")
                .values(
                    status="failed",
                    result=_STALE_RUNNING_RESULT,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        return transition_result.rowcount

    async def get_session(
        self,
        session_id: UUID,
        user_id: str | None = None,
        correlation_id: str | None = None,
    ) -> SessionStatus | None:
        query = select(SessionRecord).where(SessionRecord.session_id == session_id)
        if user_id is not None:
            query = query.where(SessionRecord.user_id == user_id)
        record = await self.session.scalar(query)
        if record is None:
            return None
        return SessionStatus(
            session_id=record.session_id,
            request_count=record.request_count,
            correlation_id=correlation_id or get_correlation_id(),
        )

    async def send_message(
        self,
        payload: RequestCreate,
        user_id: str,
        correlation_id: str,
        idempotency_key: str | None,
        approval_granted: bool = False,
        image_attachments: list[str] | None = None,
    ) -> SendMessageResponse:
        accepted = await self.create(payload, user_id, correlation_id, idempotency_key)
        result = await self.execute(
            accepted.request_id, user_id, approval_granted, image_attachments or []
        )
        if result is None:
            raise RuntimeError("Request disappeared during message execution")

        session_id = payload.session_id
        if session_id is None:
            record = await self.session.get(RequestRecord, accepted.request_id)
            if record is None:
                raise RuntimeError("Request disappeared after execution")
            session_id = record.session_id

        request_record = await self.session.get(RequestRecord, accepted.request_id)
        if request_record is None:
            raise RuntimeError("Request disappeared after execution")

        user_message = ConversationMessage(
            message_id=request_record.request_id,
            request_id=request_record.request_id,
            role="user",
            content=request_record.input,
            status=request_record.status,
            created_at=request_record.created_at,
        )
        assistant_message = None
        if request_record.result is not None:
            assistant_message = ConversationMessage(
                message_id=uuid5(request_record.request_id, "assistant"),
                request_id=request_record.request_id,
                role="assistant",
                content=request_record.result,
                status=request_record.status,
                created_at=datetime.now(UTC),
            )
        return SendMessageResponse(
            session_id=session_id,
            request_id=request_record.request_id,
            status=request_record.status,
            user_message=user_message,
            assistant_message=assistant_message,
            correlation_id=request_record.correlation_id,
            fallback_info=self._last_fallback,
        )

    async def history(
        self, session_id: UUID, user_id: str, correlation_id: str
    ) -> ConversationHistory | None:
        session_record = await self.session.get(SessionRecord, session_id)
        if session_record is None or session_record.user_id != user_id:
            return None

        result = await self.session.scalars(
            select(RequestRecord)
            .where(
                RequestRecord.session_id == session_id,
                RequestRecord.user_id == user_id,
            )
            .order_by(RequestRecord.created_at, RequestRecord.request_id)
        )
        messages: list[ConversationMessage] = []
        for record in result.all():
            messages.append(
                ConversationMessage(
                    message_id=record.request_id,
                    request_id=record.request_id,
                    role="user",
                    content=record.input,
                    status=record.status,
                    created_at=record.created_at,
                )
            )
            if record.result is not None:
                messages.append(
                    ConversationMessage(
                        message_id=uuid5(record.request_id, "assistant"),
                        request_id=record.request_id,
                        role="assistant",
                        content=record.result,
                        status=record.status,
                        created_at=record.created_at,
                    )
                )
        return ConversationHistory(
            session_id=session_id, messages=messages, correlation_id=correlation_id
        )

    async def _fetch_session_history(
        self, session_id: UUID, user_id: str, limit: int = 20
    ) -> list[dict[str, str]]:
        result = await self.session.scalars(
            select(RequestRecord)
            .where(
                RequestRecord.session_id == session_id,
                RequestRecord.user_id == user_id,
                RequestRecord.status == "completed",
            )
            .order_by(RequestRecord.created_at, RequestRecord.request_id)
        )
        history: list[dict[str, str]] = []
        for record in result.all():
            history.append({"role": "user", "content": record.input})
            if record.result is not None:
                history.append({"role": "assistant", "content": record.result})
        return history[-limit:]

    async def list_sessions(
        self, user_id: str, limit: int = 50
    ) -> list[SessionRecord]:
        result = await self.session.scalars(
            select(SessionRecord)
            .where(SessionRecord.user_id == user_id)
            .order_by(SessionRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.all())

    async def list_requests(
        self, user_id: str, limit: int = 50
    ) -> list[RequestRecord]:
        result = await self.session.scalars(
            select(RequestRecord)
            .where(RequestRecord.user_id == user_id)
            .order_by(RequestRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.all())

