"""FASE H — Authentication/Authorization acceptance test."""
from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def test_auth_acceptance() -> None:
    results = []

    def check(name, condition):
        s = "PASS" if condition else "FAIL"
        results.append((name, s))
        print(f"  [{s}] {name}")

    print("\n=== FASE H: AUTHENTICATION / AUTHORIZATION ===")

    with TestClient(app) as c:
        r = c.get("/api/v1/health/live")
        check("H1 health/live no auth needed", r.status_code == 200)

        r = c.get("/api/v1/health/ready")
        check("H2 health/ready no auth needed", r.status_code == 200)

        r = c.get("/api/v1/providers")
        check("H3 providers no auth needed", r.status_code == 200)

        r = c.post("/api/v1/requests", json={"input": "auth test"}, headers={"Idempotency-Key": "auth-1"})
        check("H4 request no auth (JWT_REQUIRED=false)", r.status_code == 202)

        r = c.post("/api/v1/sessions/00000000-0000-0000-0000-000000000001/messages", json={"input": "auth test"}, headers={"Idempotency-Key": "auth-2"})
        check("H5 session msg no auth (JWT_REQUIRED=false)", r.status_code == 200)

        r = c.get("/api/v1/sessions")
        check("H6 list sessions no auth (JWT_REQUIRED=false)", r.status_code == 200)

        r = c.get("/api/v1/requests")
        check("H7 list requests no auth (JWT_REQUIRED=false)", r.status_code == 200)

        r = c.get("/api/v1/observability/audit")
        check("H8 audit no auth (JWT_REQUIRED=false)", r.status_code == 200)

        r = c.get("/api/v1/observability/metrics")
        check("H9 metrics no auth (JWT_REQUIRED=false)", r.status_code == 200)

    import os
    os.environ["APP_ENV"] = "production"
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://user:pass@host/db"
    os.environ["JWT_SECRET"] = "change-this-local-development-secret"
    os.environ["AI_PROVIDER"] = "deterministic"

    try:
        from personal_ai_secretary.shared.config import Settings
        Settings()
        check("H10 production guard fires (JWT_SECRET default)", False)
    except Exception:
        check("H10 production guard fires (JWT_SECRET default)", True)

    os.environ["APP_ENV"] = "development"
    os.environ.pop("JWT_SECRET", None)
    os.environ.pop("DATABASE_URL", None)

    passed = sum(1 for _, s in results if s == "PASS")
    failed = sum(1 for _, s in results if s == "FAIL")
    print(f"\n  AUTH: {passed} PASS, {failed} FAIL")
    assert failed == 0, f"{failed} auth tests failed"
