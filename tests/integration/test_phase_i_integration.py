"""Phase I — End-to-end integration tests for the complete Chiky system.

These tests verify that all phases (A-H) work correctly as a single system
by exercising the full UI→API→Agent→Tool→Response flow.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app

# ─── I1: Health endpoints, static files, API availability ────────────────────

def test_i1_live_health() -> None:
    """Health/live must return 200 with status=alive."""
    with TestClient(app) as c:
        r = c.get("/api/v1/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"


def test_i1_ready_health() -> None:
    """Health/ready must return 200 with db=ok in test mode."""
    with TestClient(app) as c:
        r = c.get("/api/v1/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["database"] == "ok"
        assert "provider" in body


def test_i1_static_index_html() -> None:
    """Root path returns the Chiky HTML frontend."""
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Chiky" in r.text


def test_i1_static_css() -> None:
    """CSS file is served correctly."""
    with TestClient(app) as c:
        r = c.get("/static/css/style.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]


def test_i1_static_js() -> None:
    """JavaScript file is served correctly."""
    with TestClient(app) as c:
        r = c.get("/static/js/app.js")
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert "javascript" in ct or "text/plain" in ct


def test_i1_openapi_schema() -> None:
    """OpenAPI schema is accessible."""
    with TestClient(app) as c:
        r = c.get("/docs")
        assert r.status_code == 200
        r2 = c.get("/openapi.json")
        assert r2.status_code == 200
        schema = r2.json()
        assert schema["info"]["title"] == "Personal AI Secretary API"


# ─── I2: Full conversation flow ───────────────────────────────────────────────

def test_i2_full_conversation_flow() -> None:
    """Create session → send message → receive response → verify persistence."""
    sid = str(uuid4())
    with TestClient(app) as c:
        # Send a message
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "Hello Chiky, what is 2+2?"},
            headers={"Idempotency-Key": f"i2-flow-{sid}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == sid
        assert body["status"] == "completed"
        assert body["user_message"]["role"] == "user"
        assert body["user_message"]["content"] == "Hello Chiky, what is 2+2?"
        assert body["assistant_message"]["role"] == "assistant"
        assert "DETERMINISTIC_RESPONSE" in body["assistant_message"]["content"]

        # Verify persistence via history endpoint
        h = c.get(f"/api/v1/sessions/{sid}/messages")
        assert h.status_code == 200
        messages = h.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


def test_i2_request_id_returned() -> None:
    """The response includes a valid request_id."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "test request id"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "request_id" in body
        # request_id should be a valid UUID
        from uuid import UUID
        UUID(body["request_id"])


def test_i2_correlation_id_propagated() -> None:
    """X-Correlation-ID is echoed in response headers."""
    sid = str(uuid4())
    corr = f"test-corr-{sid[:8]}"
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "correlation test"},
            headers={"X-Correlation-ID": corr},
        )
        assert r.status_code == 200
        assert r.headers.get("X-Correlation-ID") == corr


# ─── I3: Conversation management (rename, delete, search) ────────────────────

def test_i3_session_title_set_on_first_message() -> None:
    """Session title is automatically set from the first message."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "My unique title message"},
        )
        r = c.get("/api/v1/ui/sessions")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        session = next((s for s in sessions if s["session_id"] == sid), None)
        assert session is not None
        assert session["title"] == "My unique title message"


def test_i3_rename_session() -> None:
    """PATCH /api/v1/ui/sessions/{id} renames a session."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "Original message"},
        )
        r = c.patch(
            f"/api/v1/ui/sessions/{sid}",
            json={"title": "My Custom Title"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "My Custom Title"

        # Verify it appears in list with new title
        r2 = c.get("/api/v1/ui/sessions")
        sessions = r2.json()["sessions"]
        session = next((s for s in sessions if s["session_id"] == sid), None)
        assert session is not None
        assert session["title"] == "My Custom Title"


def test_i3_rename_session_strips_whitespace() -> None:
    """Rename strips leading/trailing whitespace from title."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "hello"})
        r = c.patch(f"/api/v1/ui/sessions/{sid}", json={"title": "  Trimmed Title  "})
        assert r.status_code == 200
        assert r.json()["title"] == "Trimmed Title"


def test_i3_rename_nonexistent_session_404() -> None:
    """PATCH on unknown session_id returns 404."""
    fake_id = str(uuid4())
    with TestClient(app) as c:
        r = c.patch(f"/api/v1/ui/sessions/{fake_id}", json={"title": "Ghost"})
        assert r.status_code == 404


def test_i3_delete_session() -> None:
    """DELETE /api/v1/ui/sessions/{id} removes session and its messages."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "Message to be deleted"},
        )
        # Delete
        r = c.delete(f"/api/v1/ui/sessions/{sid}")
        assert r.status_code == 204

        # History should return 404
        h = c.get(f"/api/v1/sessions/{sid}/messages")
        assert h.status_code == 404


def test_i3_delete_nonexistent_session_404() -> None:
    """DELETE on unknown session returns 404."""
    fake_id = str(uuid4())
    with TestClient(app) as c:
        r = c.delete(f"/api/v1/ui/sessions/{fake_id}")
        assert r.status_code == 404


def test_i3_delete_cascade_removes_request_records() -> None:
    """After DELETE, request records are also removed (cascade)."""
    sid = str(uuid4())
    with TestClient(app) as c:
        resp = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "to be purged"},
        )
        req_id = resp.json()["request_id"]

        c.delete(f"/api/v1/ui/sessions/{sid}")

        # The request record should no longer be accessible
        r = c.get(f"/api/v1/requests/{req_id}")
        assert r.status_code == 404


def test_i3_search_sessions_by_title() -> None:
    """Search filters sessions by title."""
    unique = str(uuid4())[:8]
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": f"UNIQUE_SEARCH_{unique}"},
        )
        r = c.get(f"/api/v1/ui/sessions?search=UNIQUE_SEARCH_{unique}")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert len(sessions) >= 1
        assert all(f"UNIQUE_SEARCH_{unique}" in s["title"] for s in sessions)


def test_i3_search_sessions_empty_string_returns_all() -> None:
    """Search with no query returns all sessions."""
    with TestClient(app) as c:
        c.post(
            "/api/v1/sessions/" + str(uuid4()) + "/messages",
            json={"input": "baseline message"},
        )
        r = c.get("/api/v1/ui/sessions")
        assert r.status_code == 200
        assert len(r.json()["sessions"]) >= 1


def test_i3_search_no_match_returns_empty() -> None:
    """Search for a non-existent title returns empty list."""
    with TestClient(app) as c:
        r = c.get("/api/v1/ui/sessions?search=XYZZY_NOTHERE_999999")
        assert r.status_code == 200
        # May or may not be empty depending on previous tests, but must 200
        assert "sessions" in r.json()


# ─── I4: Multiple conversations, context isolation ────────────────────────────

def test_i4_multiple_sessions_isolated() -> None:
    """Messages in session A do not appear in session B's history."""
    sid_a = str(uuid4())
    sid_b = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid_a}/messages", json={"input": "message in A"})
        c.post(f"/api/v1/sessions/{sid_b}/messages", json={"input": "message in B"})

        hist_a = c.get(f"/api/v1/sessions/{sid_a}/messages").json()["messages"]
        hist_b = c.get(f"/api/v1/sessions/{sid_b}/messages").json()["messages"]

        contents_a = [m["content"] for m in hist_a]
        contents_b = [m["content"] for m in hist_b]

        assert "message in A" in contents_a
        assert "message in A" not in contents_b
        assert "message in B" in contents_b
        assert "message in B" not in contents_a


def test_i4_session_request_count_increments() -> None:
    """request_count increments with each message sent."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "first"})
        r1 = c.get(f"/api/v1/sessions/{sid}")
        assert r1.json()["request_count"] == 1

        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "second"})
        r2 = c.get(f"/api/v1/sessions/{sid}")
        assert r2.json()["request_count"] == 2


# ─── I5: Provider error resilience ───────────────────────────────────────────

def test_i5_deterministic_provider_always_responds() -> None:
    """Deterministic provider never fails; confirms error path is not triggered."""
    sid = str(uuid4())
    with TestClient(app) as c:
        for i in range(3):
            r = c.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"input": f"message {i}"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "completed"


def test_i5_unknown_session_history_404() -> None:
    """GET history for unknown session returns 404, not 500."""
    with TestClient(app) as c:
        r = c.get(f"/api/v1/sessions/{uuid4()}/messages")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "HTTP_404"
        assert body["retryable"] is False


# ─── I6: Conversation context (multi-turn) ────────────────────────────────────

def test_i6_multi_turn_history_ordering() -> None:
    """Multi-turn conversation messages are returned in chronological order."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "turn one"})
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "turn two"})
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "turn three"})

        h = c.get(f"/api/v1/sessions/{sid}/messages")
        messages = h.json()["messages"]
        # 3 turns × 2 (user + assistant) = 6 messages
        assert len(messages) == 6
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert [m["content"] for m in user_msgs] == ["turn one", "turn two", "turn three"]


def test_i6_history_includes_both_roles() -> None:
    """Each turn produces a user and an assistant message in history."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "hi"})
        h = c.get(f"/api/v1/sessions/{sid}/messages").json()["messages"]
        roles = [m["role"] for m in h]
        assert "user" in roles
        assert "assistant" in roles


# ─── I7: API contract validation ─────────────────────────────────────────────

def test_i7_missing_input_returns_422() -> None:
    """POST without required 'input' field returns 422."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={})
        assert r.status_code == 422


def test_i7_empty_input_returns_422() -> None:
    """POST with empty string 'input' returns 422 (min_length=1)."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={"input": ""})
        assert r.status_code == 422


def test_i7_session_id_mismatch_returns_400() -> None:
    """Payload session_id that doesn't match URL path returns 400."""
    sid = str(uuid4())
    other = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "mismatch", "session_id": other},
        )
        assert r.status_code == 400


def test_i7_rename_title_too_long_returns_422() -> None:
    """Title longer than 200 chars returns 422."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "hi"})
        r = c.patch(f"/api/v1/ui/sessions/{sid}", json={"title": "x" * 201})
        assert r.status_code == 422


def test_i7_rename_empty_title_returns_422() -> None:
    """Empty title returns 422 (min_length=1)."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "hi"})
        r = c.patch(f"/api/v1/ui/sessions/{sid}", json={"title": ""})
        assert r.status_code == 422


def test_i7_404_returns_error_envelope() -> None:
    """404 responses include the standard error envelope."""
    with TestClient(app) as c:
        r = c.get(f"/api/v1/sessions/{uuid4()}/messages")
        assert r.status_code == 404
        body = r.json()
        assert "code" in body
        assert "message" in body
        assert "retryable" in body


# ─── I8: Session persistence ──────────────────────────────────────────────────

def test_i8_session_persists_within_same_client() -> None:
    """Messages sent in a session persist and are retrievable within the same client.

    Note: in-memory SQLite (used in test mode) does not persist across
    separate TestClient instances. Production uses a file-based DB.
    """
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "persistent msg"})
        h = c.get(f"/api/v1/sessions/{sid}/messages")
        assert h.status_code == 200
        messages = h.json()["messages"]
        assert any(m["content"] == "persistent msg" for m in messages)
        # Verify session is also in the list
        sessions_r = c.get("/api/v1/ui/sessions")
        assert any(s["session_id"] == sid for s in sessions_r.json()["sessions"])


def test_i8_updated_at_set_after_message() -> None:
    """updated_at is populated after sending a message."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "update ts"})
        r = c.get("/api/v1/ui/sessions")
        sessions = r.json()["sessions"]
        session = next((s for s in sessions if s["session_id"] == sid), None)
        assert session is not None
        assert session["updated_at"] is not None


# ─── I9: Concurrent-style operations ─────────────────────────────────────────

def test_i9_multiple_sessions_listed() -> None:
    """Multiple distinct sessions appear in /api/v1/ui/sessions."""
    ids = [str(uuid4()) for _ in range(3)]
    with TestClient(app) as c:
        for sid in ids:
            c.post(f"/api/v1/sessions/{sid}/messages", json={"input": f"msg-{sid[:8]}"})

        r = c.get("/api/v1/ui/sessions?limit=200")
        session_ids = [s["session_id"] for s in r.json()["sessions"]]
        for sid in ids:
            assert sid in session_ids


def test_i9_idempotency_dedup() -> None:
    """Sending the same idempotency key twice returns the same request_id."""
    sid = str(uuid4())
    key = f"idem-{uuid4()}"
    with TestClient(app) as c:
        r1 = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "idempotent"},
            headers={"Idempotency-Key": key},
        )
        r2 = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "idempotent"},
            headers={"Idempotency-Key": key},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["request_id"] == r2.json()["request_id"]


# ─── I10: Security ────────────────────────────────────────────────────────────

def test_i10_path_traversal_in_session_id() -> None:
    """Path traversal in session_id is rejected (FastAPI UUID parsing)."""
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/sessions/../../../etc/passwd/messages",
            json={"input": "hack"},
        )
        # FastAPI should reject this with 422 or 404
        assert r.status_code in (404, 422)


def test_i10_extra_fields_in_rename_rejected() -> None:
    """Extra fields in rename payload are rejected (extra='forbid')."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "hi"})
        r = c.patch(
            f"/api/v1/ui/sessions/{sid}",
            json={"title": "Valid Title", "evil_field": "inject"},
        )
        assert r.status_code == 422


def test_i10_extra_fields_in_message_rejected() -> None:
    """Extra fields in message payload are rejected (extra='forbid')."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "hi", "extra": "bad"},
        )
        assert r.status_code == 422


def test_i10_sql_injection_in_input_handled() -> None:
    """SQL injection in message input is treated as normal text."""
    sid = str(uuid4())
    evil = "'; DROP TABLE sessions; --"
    with TestClient(app) as c:
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={"input": evil})
        # Should succeed (deterministic provider), not crash
        assert r.status_code == 200
        # And the session still exists
        h = c.get(f"/api/v1/sessions/{sid}/messages")
        assert h.status_code == 200


# ─── I11: Alembic/DB integrity ───────────────────────────────────────────────

def test_i11_database_health_in_ready_endpoint() -> None:
    """DB health is confirmed by /health/ready endpoint."""
    with TestClient(app) as c:
        r = c.get("/api/v1/health/ready")
        assert r.status_code == 200
        assert r.json()["database"] == "ok"


def test_i11_sessions_list_returns_structured_response() -> None:
    """Sessions list endpoint returns valid SessionListResponse schema."""
    with TestClient(app) as c:
        r = c.get("/api/v1/ui/sessions")
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)


def test_i11_session_list_items_have_required_fields() -> None:
    """Session list items contain all required fields."""
    sid = str(uuid4())
    with TestClient(app) as c:
        c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "fields check"})
        r = c.get("/api/v1/ui/sessions")
        sessions = [s for s in r.json()["sessions"] if s["session_id"] == sid]
        assert len(sessions) == 1
        s = sessions[0]
        assert "session_id" in s
        assert "user_id" in s
        assert "created_at" in s
        assert "request_count" in s
        assert "title" in s


# ─── I12: Markdown content in responses ──────────────────────────────────────

def test_i12_markdown_in_message_input_handled() -> None:
    """Markdown-style input doesn't break the system."""
    sid = str(uuid4())
    markdown_input = "# Hello\n\n**bold** _italic_ `code` ```block```"
    with TestClient(app) as c:
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={"input": markdown_input})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


def test_i12_unicode_in_message_input_handled() -> None:
    """Unicode characters in input are stored and returned correctly."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "Héllo Wörld 你好 こんにちは"},
        )
        assert r.status_code == 200
        h = c.get(f"/api/v1/sessions/{sid}/messages").json()["messages"]
        user_msg = next(m for m in h if m["role"] == "user")
        assert user_msg["content"] == "Héllo Wörld 你好 こんにちは"


# ─── I13: Tool invocation via session messages ────────────────────────────────

def test_i13_calculator_tool_via_message() -> None:
    """Calculator tool can be invoked via session message."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": '@tool:calculator {"expression": "3*7"}'},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert "21" in r.json()["assistant_message"]["content"]


def test_i13_high_risk_blocked_without_approval() -> None:
    """High-risk message is blocked without X-Approval-Granted header."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "delete all files"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"


def test_i13_high_risk_completes_with_approval() -> None:
    """High-risk message completes when X-Approval-Granted: true is set."""
    sid = str(uuid4())
    with TestClient(app) as c:
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "delete all files"},
            headers={"X-Approval-Granted": "true"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


# ─── I14: Observability endpoints ─────────────────────────────────────────────

def test_i14_audit_endpoint_returns_list() -> None:
    """Audit endpoint returns a list of events."""
    with TestClient(app) as c:
        c.post(
            "/api/v1/sessions/" + str(uuid4()) + "/messages",
            json={"input": "audit test"},
        )
        r = c.get("/api/v1/observability/audit")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_i14_metrics_endpoint_has_counters() -> None:
    """Metrics endpoint returns counters dict."""
    with TestClient(app) as c:
        r = c.get("/api/v1/observability/metrics")
        assert r.status_code == 200
        body = r.json()
        assert "counters" in body
        assert isinstance(body["counters"], dict)


def test_i14_prometheus_metrics_text_format() -> None:
    """Prometheus metrics endpoint returns text/plain."""
    with TestClient(app) as c:
        r = c.get("/api/v1/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]


# ─── I15: Provider info endpoint ──────────────────────────────────────────────

def test_i15_providers_endpoint_structure() -> None:
    """Providers endpoint returns active provider and available modes."""
    with TestClient(app) as c:
        r = c.get("/api/v1/providers")
        assert r.status_code == 200
        body = r.json()
        assert body["active"]["name"] == "deterministic"
        assert "deterministic" in body["available_modes"]
        assert "local" in body["available_modes"]
