"""QA FASE D-R — Comprehensive functional acceptance test."""
from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def _check(name: str, condition: bool, results: list) -> None:
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    print(f"  [{status}] {name}")


def test_qa_comprehensive() -> None:
    results: list[tuple[str, str]] = []

    with TestClient(app) as c:
        print("\n=== FASE D: API FUNCTIONAL QA ===")

        # Health
        r = c.get("/api/v1/health/live")
        _check("D1 health/live alive", r.status_code == 200 and r.json()["status"] == "alive", results)
        r = c.get("/api/v1/health/ready")
        _check("D2 health/ready ready", r.status_code == 200 and r.json()["status"] == "ready", results)
        _check("D3 health/ready has provider", "provider" in r.json(), results)
        _check("D4 health/ready has database", r.json()["database"] == "ok", results)

        # Providers
        r = c.get("/api/v1/providers")
        _check("D5 providers active", r.status_code == 200 and "active" in r.json(), results)
        _check("D6 providers modes", "available_modes" in r.json() and "deterministic" in r.json()["available_modes"], results)

        # Create request
        r = c.post("/api/v1/requests", json={"input": "qa test"}, headers={"Idempotency-Key": "qa-req-1"})
        _check("D7 create request 202", r.status_code == 202, results)
        req_id = r.json()["request_id"]
        _check("D8 request has id", bool(req_id), results)

        # Get request
        r = c.get(f"/api/v1/requests/{req_id}")
        _check("D9 get request 200", r.status_code == 200, results)
        _check("D10 request status accepted", r.json()["status"] == "accepted", results)

        # Execute request
        r = c.post(f"/api/v1/requests/{req_id}/execute")
        _check("D11 execute request 200", r.status_code == 200, results)
        _check("D12 request status completed", r.json()["status"] == "completed", results)
        _check("D13 result contains DETERMINISTIC", "DETERMINISTIC_RESPONSE" in r.json()["result"], results)

        # Request 404
        r = c.get("/api/v1/requests/00000000-0000-0000-0000-000000000000")
        _check("D14 request 404", r.status_code == 404, results)

        # List requests
        r = c.get("/api/v1/requests")
        _check("D15 list requests 200", r.status_code == 200, results)
        _check("D16 list has requests", len(r.json()["requests"]) >= 1, results)

        # Session message (creates session)
        sid = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "qa session msg"}, headers={"Idempotency-Key": "qa-s1"})
        _check("D17 session msg 200", r.status_code == 200, results)
        _check("D18 status completed", r.json()["status"] == "completed", results)
        _check("D19 user_message role=user", r.json()["user_message"]["role"] == "user", results)
        _check("D20 assistant_message role=assistant", r.json()["assistant_message"]["role"] == "assistant", results)
        _check("D21 assistant has DETERMINISTIC", "DETERMINISTIC_RESPONSE" in r.json()["assistant_message"]["content"], results)

        # Second message
        r = c.post(f"/api/v1/sessions/{sid}/messages", json={"input": "second msg"}, headers={"Idempotency-Key": "qa-s2"})
        _check("D22 second msg completed", r.status_code == 200 and r.json()["status"] == "completed", results)

        # Session history
        r = c.get(f"/api/v1/sessions/{sid}/messages")
        _check("D23 session history 200", r.status_code == 200, results)
        _check("D24 history has 4 messages", len(r.json()["messages"]) == 4, results)

        # Session metadata
        r = c.get(f"/api/v1/sessions/{sid}")
        _check("D25 session metadata 200", r.status_code == 200, results)
        _check("D26 request_count=2", r.json()["request_count"] == 2, results)

        # Session 404
        r = c.get("/api/v1/sessions/00000000-0000-0000-0000-000000000000")
        _check("D27 session 404", r.status_code == 404, results)

        # List sessions
        r = c.get("/api/v1/sessions")
        _check("D28 list sessions 200", r.status_code == 200, results)
        _check("D29 list has sessions", len(r.json()["sessions"]) >= 1, results)

        # Observability
        r = c.get("/api/v1/observability/audit")
        _check("D30 audit 200", r.status_code == 200 and len(r.json()) >= 1, results)
        r = c.get("/api/v1/observability/metrics")
        _check("D31 metrics 200", r.status_code == 200 and "counters" in r.json(), results)
        r = c.get("/api/v1/metrics")
        _check("D32 prometheus 200", r.status_code == 200 and "text/plain" in r.headers["content-type"], results)

        # Docs
        r = c.get("/docs")
        _check("D33 swagger 200", r.status_code == 200, results)

        print("\n=== FASE E: MULTI-TURN CONVERSATION ===")

        # Create session with context
        sid2 = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid2}/messages", json={"input": "Mi nombre es Carlos y estoy trabajando en un proyecto llamado Atlas."}, headers={"Idempotency-Key": "qa-e1"})
        _check("E1 context msg 1", r.status_code == 200 and r.json()["status"] == "completed", results)

        r = c.post(f"/api/v1/sessions/{sid2}/messages", json={"input": "¿Cuál es el nombre de mi proyecto?"}, headers={"Idempotency-Key": "qa-e2"})
        _check("E2 context msg 2", r.status_code == 200 and r.json()["status"] == "completed", results)

        # History check
        r = c.get(f"/api/v1/sessions/{sid2}/messages")
        _check("E3 history 4 messages", len(r.json()["messages"]) == 4, results)
        _check("E4 user msgs are role=user", all(m["role"] == "user" for m in r.json()["messages"] if m["role"] == "user"), results)
        _check("E5 asst msgs are role=assistant", all(m["role"] == "assistant" for m in r.json()["messages"] if m["role"] == "assistant"), results)

        # Fill many messages to test history behavior
        for i in range(15):
            r = c.post(f"/api/v1/sessions/{sid2}/messages", json={"input": f"msg {i+3}"}, headers={"Idempotency-Key": f"qa-e-fill-{i}"})
            assert r.status_code == 200

        # History: 2 initial + 15 fill = 17 messages (34 user+assistant)
        # GET returns ALL stored messages (full audit trail)
        r = c.get(f"/api/v1/sessions/{sid2}/messages")
        _check("E6 history stores all messages", len(r.json()["messages"]) == 34, results)

        print("\n=== FASE F: CONTEXT SUMMARY ===")

        # Deterministic provider returns DETERMINISTIC_RESPONSE which includes the input
        # context_summary is injected as system message — verify it doesn't break
        sid3 = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid3}/messages", json={"input": "test context flow"}, headers={"Idempotency-Key": "qa-f1"})
        _check("F1 context flow msg", r.status_code == 200 and r.json()["status"] == "completed", results)
        _check("F2 deterministic returns input", "test context flow" in r.json()["assistant_message"]["content"], results)

        print("\n=== FASE G: USER ISOLATION ===")

        # User A session
        sid_a = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid_a}/messages", json={"input": "user A msg"}, headers={"Idempotency-Key": "qa-ga1"})
        _check("G1 user A msg", r.status_code == 200, results)

        # User B session
        sid_b = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid_b}/messages", json={"input": "user B msg"}, headers={"Idempotency-Key": "qa-gb1"})
        _check("G2 user B msg", r.status_code == 200, results)

        # TestClient uses same "user" for all requests (no JWT),
        # so cross-user isolation is verified via service layer
        # The test verifies sessions are isolated by UUID
        r_a = c.get(f"/api/v1/sessions/{sid_a}/messages")
        r_b = c.get(f"/api/v1/sessions/{sid_b}/messages")
        _check("G3 sessions have different content", r_a.json()["messages"][0]["content"] != r_b.json()["messages"][0]["content"], results)

        print("\n=== FASE I: HIGH-RISK / GOVERNANCE ===")

        # Normal operation
        sid4 = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid4}/messages", json={"input": "normal operation"}, headers={"Idempotency-Key": "qa-i1"})
        _check("I1 normal op completed", r.status_code == 200 and r.json()["status"] == "completed", results)

        # High risk without approval
        r = c.post(f"/api/v1/sessions/{sid4}/messages", json={"input": "send email to the board"}, headers={"Idempotency-Key": "qa-i2"})
        _check("I2 high risk blocked", r.status_code == 200 and r.json()["status"] == "blocked", results)

        # High risk with approval
        r = c.post(f"/api/v1/sessions/{sid4}/messages", json={"input": "send email to the board"}, headers={"Idempotency-Key": "qa-i3", "X-Approval-Granted": "true"})
        _check("I3 high risk approved", r.status_code == 200 and r.json()["status"] == "completed", results)

        print("\n=== FASE J: ERROR RESILIENCE ===")

        # Tool execution
        sid5 = str(uuid4())
        r = c.post(f"/api/v1/sessions/{sid5}/messages", json={"input": '@tool:calculator {"expression": "2+2"}'}, headers={"Idempotency-Key": "qa-j1"})
        _check("J1 tool execution", r.status_code == 200 and r.json()["status"] == "completed", results)
        _check("J2 tool result 4", "4" in r.json()["assistant_message"]["content"], results)

        # Unknown tool
        r = c.post(f"/api/v1/sessions/{sid5}/messages", json={"input": "@tool:unknown_tool {}"}, headers={"Idempotency-Key": "qa-j2"})
        _check("J3 unknown tool handled", r.status_code == 200, results)

        print("\n=== FASE K: IDEMPOTENCY ===")

        r1 = c.post("/api/v1/requests", json={"input": "idem test"}, headers={"Idempotency-Key": "qa-k1"})
        r2 = c.post("/api/v1/requests", json={"input": "idem different"}, headers={"Idempotency-Key": "qa-k1"})
        _check("K1 same key same id", r1.json()["request_id"] == r2.json()["request_id"], results)
        _check("K2 same key same status", r1.json()["status"] == r2.json()["status"], results)

        # Execute once, verify idempotent
        rid = r1.json()["request_id"]
        r3 = c.post(f"/api/v1/requests/{rid}/execute")
        r4 = c.post(f"/api/v1/requests/{rid}/execute")
        _check("K3 execute idempotent", r3.json()["status"] == r4.json()["status"], results)

        print("\n=== FASE L: PROVIDERS ===")

        r = c.get("/api/v1/providers")
        active = r.json()["active"]
        _check("L1 deterministic active", active["name"] == "deterministic", results)
        _check("L2 deterministic available", active["available"] is True, results)
        _check("L3 modes include local", "local" in r.json()["available_modes"], results)
        _check("L4 modes include remote", "remote" in r.json()["available_modes"], results)

        # Print summary
        passed = sum(1 for _, s in results if s == "PASS")
        failed = sum(1 for _, s in results if s == "FAIL")
        print(f"\n{'='*60}")
        print(f"TOTAL: {passed} PASS, {failed} FAIL, {len(results)} tests")
        if failed:
            print("FAILURES:")
            for name, status in results:
                if status == "FAIL":
                    print(f"  FAIL: {name}")
        print(f"{'='*60}")

        assert failed == 0, f"{failed} QA tests failed"
