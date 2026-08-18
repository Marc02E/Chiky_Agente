"""21H — Application Functional Release Test (end-to-end smoke)."""
from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def test_full_functional_smoke() -> None:
    with TestClient(app) as c:
        # 1. Startup / health
        r = c.get("/api/v1/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

        r = c.get("/api/v1/health/ready")
        assert r.status_code in (200, 503)

        # 2. Providers
        r = c.get("/api/v1/providers")
        assert r.status_code == 200
        assert "active" in r.json()

        # 3. Create request
        r = c.post(
            "/api/v1/requests",
            json={"input": "hello"},
            headers={"Idempotency-Key": "func-req-1"},
        )
        assert r.status_code == 202
        req_id = r.json()["request_id"]

        # 4. Get request
        r = c.get(f"/api/v1/requests/{req_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

        # 5. Execute request
        r = c.post(f"/api/v1/requests/{req_id}/execute")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert "DETERMINISTIC_RESPONSE" in r.json()["result"]

        # 6. Session message (creates session)
        sid = str(uuid4())
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "hello session"},
            headers={"Idempotency-Key": "func-s1"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert r.json()["user_message"]["role"] == "user"
        assert r.json()["assistant_message"]["role"] == "assistant"

        # 7. Second message (uses history)
        r = c.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "second message"},
            headers={"Idempotency-Key": "func-s2"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

        # 8. Session history
        r = c.get(f"/api/v1/sessions/{sid}/messages")
        assert r.status_code == 200
        assert len(r.json()["messages"]) == 4

        # 9. Session metadata
        r = c.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["request_count"] == 2

        # 10. List sessions
        r = c.get("/api/v1/sessions")
        assert r.status_code == 200
        assert len(r.json()["sessions"]) >= 1

        # 11. List requests
        r = c.get("/api/v1/requests")
        assert r.status_code == 200
        assert len(r.json()["requests"]) >= 1

        # 12. Audit
        r = c.get("/api/v1/observability/audit")
        assert r.status_code == 200
        assert len(r.json()) >= 1

        # 13. Metrics
        r = c.get("/api/v1/observability/metrics")
        assert r.status_code == 200
        assert "counters" in r.json()

        # 14. Prometheus metrics
        r = c.get("/api/v1/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]

        # 15. High risk blocks without approval
        sid2 = str(uuid4())
        r = c.post(
            f"/api/v1/sessions/{sid2}/messages",
            json={"input": "send email to the board"},
            headers={"Idempotency-Key": "func-blocked"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

        # 16. High risk completes with approval
        r = c.post(
            f"/api/v1/sessions/{sid2}/messages",
            json={"input": "send email to the board"},
            headers={
                "Idempotency-Key": "func-approved",
                "X-Approval-Granted": "true",
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

        # 17. Tool execution
        sid3 = str(uuid4())
        r = c.post(
            f"/api/v1/sessions/{sid3}/messages",
            json={"input": '@tool:calculator {"expression": "2+2"}'},
            headers={"Idempotency-Key": "func-tool"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert "4" in r.json()["assistant_message"]["content"]

        # 18. Idempotency
        r1 = c.post(
            "/api/v1/requests",
            json={"input": "idem"},
            headers={"Idempotency-Key": "func-idem"},
        )
        r2 = c.post(
            "/api/v1/requests",
            json={"input": "idem2"},
            headers={"Idempotency-Key": "func-idem"},
        )
        assert r1.json()["request_id"] == r2.json()["request_id"]

        # 19. 404 handling
        r = c.get("/api/v1/requests/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

        # 20. Swagger docs
        r = c.get("/docs")
        assert r.status_code == 200
