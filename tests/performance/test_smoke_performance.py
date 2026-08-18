import time
from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app

REQUESTS = 15
P95_BOUND_SECONDS = 2.0


def test_request_cycle_smoke_latency() -> None:
    timings: list[float] = []
    with TestClient(app) as client:
        for _ in range(REQUESTS):
            started = time.perf_counter()
            response = client.post(
                f"/api/v1/sessions/{uuid4()}/messages",
                headers={"Idempotency-Key": str(uuid4())},
                json={"input": "hello"},
            )
            timings.append(time.perf_counter() - started)
            assert response.status_code == 200

    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 < P95_BOUND_SECONDS