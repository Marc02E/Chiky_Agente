"""UI-specific API routes for the web interface."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete as sqla_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_ai_secretary.domain.contracts import (
    AttachedFile,
    SessionListItem,
    SessionListResponse,
    SessionRenameRequest,
)
from personal_ai_secretary.domain.models import RequestRecord, SessionRecord
from personal_ai_secretary.infrastructure.database import get_db
from personal_ai_secretary.shared.auth import require_bearer_token

router = APIRouter(prefix="/api/v1/ui", tags=["ui"])


def _user_id(claims: dict[str, object]) -> str:
    value = claims.get("sub")
    return str(value) if value else "development-user"


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions_with_titles(
    limit: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=200),
    claims: dict[str, object] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """List sessions enriched with title, supporting optional title search."""
    user_id = _user_id(claims)
    query = (
        select(SessionRecord)
        .where(SessionRecord.user_id == user_id)
        .order_by(SessionRecord.updated_at.desc().nulls_last(), SessionRecord.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    items: list[SessionListItem] = []
    for s in sessions:
        title = s.title
        if not title:
            first_msg = await db.execute(
                select(RequestRecord.input)
                .where(
                    RequestRecord.session_id == s.session_id,
                    RequestRecord.user_id == user_id,
                )
                .order_by(RequestRecord.created_at.asc())
                .limit(1)
            )
            row = first_msg.first()
            title = (row[0][:80] if row and row[0] else None) or "New conversation"

        if search and search.lower() not in title.lower():
            continue

        items.append(
            SessionListItem(
                session_id=s.session_id,
                user_id=s.user_id,
                created_at=s.created_at,
                updated_at=s.updated_at,
                request_count=s.request_count or 0,
                title=title,
            )
        )

    return SessionListResponse(sessions=items)


@router.patch("/sessions/{session_id}", response_model=SessionListItem)
async def rename_session(
    session_id: UUID,
    payload: SessionRenameRequest,
    claims: dict[str, object] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> SessionListItem:
    """Rename a session with a custom title."""
    user_id = _user_id(claims)
    record = await db.get(SessionRecord, session_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    record.title = payload.title.strip()
    record.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(record)
    return SessionListItem(
        session_id=record.session_id,
        user_id=record.user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        request_count=record.request_count or 0,
        title=record.title,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    claims: dict[str, object] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a session and all its messages."""
    user_id = _user_id(claims)
    record = await db.get(SessionRecord, session_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.execute(
        sqla_delete(RequestRecord).where(
            RequestRecord.session_id == session_id,
            RequestRecord.user_id == user_id,
        )
    )
    await db.delete(record)
    await db.commit()


_MAX_TEXT_UPLOAD_BYTES = 5_000
_MAX_PDF_UPLOAD_BYTES = 10_000_000  # 10MB
_ALLOWED_UPLOAD_MIME_PREFIXES: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/xml",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)


@router.post("/upload", response_model=AttachedFile, status_code=200)
async def upload_file(
    file: UploadFile = File(...),
    claims: dict[str, object] = Depends(require_bearer_token),
) -> AttachedFile:
    """Accept a file upload and return its content for use as an attachment.

    Supports text files (up to 5KB), PDFs (up to 10MB with text extraction),
    and DOCX files (up to 5MB with text extraction).
    """
    content_type = file.content_type or "text/plain"
    if not any(content_type.startswith(p) for p in _ALLOWED_UPLOAD_MIME_PREFIXES):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Accepted: text, JSON, XML, PDF, DOCX.",
        )

    # Determine size limit based on file type
    is_pdf = content_type == "application/pdf"
    is_docx = "wordprocessingml" in content_type
    max_bytes = _MAX_PDF_UPLOAD_BYTES if (is_pdf or is_docx) else _MAX_TEXT_UPLOAD_BYTES

    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_bytes} bytes.",
        )

    # For PDFs and DOCX, extract text content
    if is_pdf or is_docx:
        import os
        import tempfile

        from personal_ai_secretary.context.document_intelligence import (
            extract_docx_content,
            extract_pdf_content,
        )

        suffix = ".pdf" if is_pdf else ".docx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        try:
            if is_pdf:
                doc_info = extract_pdf_content(tmp_path, max_chars=50_000)
            else:
                doc_info = extract_docx_content(tmp_path, max_chars=50_000)
            text = doc_info.content
        finally:
            os.unlink(tmp_path)

        if not text or text.startswith("[PDF extraction") or text.startswith("[DOCX extraction"):
            raise HTTPException(
                status_code=422,
                detail=f"Could not extract text from {file.filename}. {text}",
            )
    else:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Could not decode file as UTF-8.") from exc

    return AttachedFile(
        name=file.filename or "file",
        content=text,
        size=len(raw),
        mime_type=content_type,
    )
