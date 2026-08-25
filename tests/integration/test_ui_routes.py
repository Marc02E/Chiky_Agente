"""Tests for the UI-specific routes and static file serving."""

from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def test_index_returns_html():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Chiky — Personal AI Secretary" in r.text


def test_static_css():
    with TestClient(app) as c:
        r = c.get("/static/css/style.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]
        assert "--bg-primary" in r.text


def test_static_js():
    with TestClient(app) as c:
        r = c.get("/static/js/app.js")
        assert r.status_code == 200
        assert (
            "javascript" in r.headers["content-type"]
            or "text/javascript" in r.headers["content-type"]
        )
        assert "Chiky — Personal AI Secretary" in r.text


def test_ui_sessions_empty():
    with TestClient(app) as c:
        r = c.get("/api/v1/ui/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data


def test_ui_sessions_after_message():
    with TestClient(app) as c:
        sid = str(uuid4())
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "Hello from UI test"},
            headers={"Idempotency-Key": f"ui-test-{sid}"},
        )
        assert r.status_code == 200

        r2 = c.get("/api/v1/ui/sessions")
        assert r2.status_code == 200
        sessions = r2.json()["sessions"]
        assert len(sessions) >= 1

        first = sessions[0]
        assert "title" in first
        assert first["title"] == "Hello from UI test"
        assert first["session_id"] == sid


def test_ui_sessions_limit():
    with TestClient(app) as c:
        r = c.get("/api/v1/ui/sessions?limit=1")
        assert r.status_code == 200


def test_swagger_still_accessible():
    with TestClient(app) as c:
        r = c.get("/docs")
        assert r.status_code == 200


def test_api_endpoints_still_work():
    with TestClient(app) as c:
        r = c.get("/api/v1/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"


# ─── Upload endpoint ───

def test_upload_text_file():
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("hello.txt", b"Hello Esteban", "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "hello.txt"
        assert data["content"] == "Hello Esteban"
        assert data["size"] == 13
        assert "text/plain" in data["mime_type"]


def test_upload_json_file():
    with TestClient(app) as c:
        payload = b'{"key": "value"}'
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("data.json", payload, "application/json")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "data.json"
        assert '"key"' in data["content"]


def test_upload_python_file():
    with TestClient(app) as c:
        code = b"def hello():\n    return 'world'\n"
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("script.py", code, "text/x-python")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "def hello" in data["content"]


def test_upload_unsupported_type_returns_415():
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("img.png", b"\x89PNG\r\n", "image/png")},
        )
        assert r.status_code == 415


def test_upload_too_large_returns_413():
    with TestClient(app) as c:
        big = b"x" * 6000  # over 5KB limit
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("big.txt", big, "text/plain")},
        )
        assert r.status_code == 413


def test_upload_empty_file():
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/ui/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["content"] == ""
        assert data["size"] == 0


# ─── Approval prefix parsing in send_session_message ───

def test_send_message_response_has_approval_request_field_when_blocked():
    """When the deterministic provider returns an APPROVAL_REQUIRED prefix,
    the endpoint must surface approval_request and strip the prefix from content."""

    with TestClient(app) as c:
        sid = str(uuid4())
        # Use @tool: syntax to trigger the user-tool path via the deterministic provider.
        # We can't easily trigger LLM approval flow in tests (no real LLM).
        # Instead verify the response schema includes approval_request=None by default.
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "hello"},
            headers={"Idempotency-Key": f"approval-test-{sid}"},
        )
        assert r.status_code == 200
        data = r.json()
        # approval_request field must always be present in the schema (None when not needed)
        assert "approval_request" in data


def test_approval_required_prefix_constant_is_importable():
    """APPROVAL_REQUIRED_PREFIX must be importable from agents.builtin."""
    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX
    assert APPROVAL_REQUIRED_PREFIX.startswith("__APPROVAL")


def test_send_message_strips_approval_prefix_from_response():
    """When the result's assistant_message contains the APPROVAL_REQUIRED_PREFIX,
    the endpoint must strip it and return approval_request in the response."""
    import json
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX
    from personal_ai_secretary.domain.contracts import (
        ConversationMessage,
        SendMessageResponse,
    )

    session_id = uuid4()
    request_id = uuid4()
    now = datetime.now(UTC)

    approval_data = json.dumps({"tool_name": "create_file", "tool_args": {"path": "/tmp/x.txt"}})
    raw_content = f"{APPROVAL_REQUIRED_PREFIX}{approval_data}\n\nI need your permission."

    mock_result = SendMessageResponse(
        session_id=session_id,
        request_id=request_id,
        status="completed",
        user_message=ConversationMessage(
            message_id=request_id,
            request_id=request_id,
            role="user",
            content="create x.txt",
            status="completed",
            created_at=now,
        ),
        assistant_message=ConversationMessage(
            message_id=request_id,
            request_id=request_id,
            role="assistant",
            content=raw_content,
            status="completed",
            created_at=now,
        ),
        correlation_id="test-corr",
    )

    with TestClient(app) as c:
        with patch(
            "personal_ai_secretary.application.service.RequestService.send_message",
            new=AsyncMock(return_value=mock_result),
        ):
            sid = str(session_id)
            r = c.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": "create x.txt"},
                headers={"Idempotency-Key": f"approval-strip-{sid}"},
            )
            assert r.status_code == 200
            data = r.json()
            # The prefix must be stripped from assistant_message content
            assert APPROVAL_REQUIRED_PREFIX not in (data.get("assistant_message") or {}).get("content", "")
            # approval_request must be populated
            assert data.get("approval_request") is not None
            assert data["approval_request"]["tool_name"] == "create_file"
            # Human-readable part preserved
            assert "I need your permission" in data["assistant_message"]["content"]
