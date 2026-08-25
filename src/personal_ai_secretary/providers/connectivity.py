"""Internet connectivity detection for adaptive routing.

FASE T: Determines whether cloud providers are reachable so the model
manager can make informed routing decisions. Never blocks; fast timeout.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger("personal_ai_secretary.providers.connectivity")

_CHECK_TIMEOUT = 3.0
_CHECK_URLS: tuple[str, ...] = (
    "https://dns.google/resolve?name=google.com",
    "https://1.1.1.1/dns-query?name=google.com",
)
_CACHE_TTL_SECONDS = 30.0


@dataclass(slots=True)
class ConnectivityStatus:
    online: bool = False
    last_checked: float = 0.0
    latency_ms: float = 0.0
    check_count: int = 0
    fail_count: int = 0


class ConnectivityChecker:
    """Non-blocking internet connectivity checker with caching."""

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS) -> None:
        self._cache_ttl = cache_ttl
        self._status = ConnectivityStatus()
        self._check_task: asyncio.Task[bool] | None = None

    @property
    def is_online(self) -> bool:
        if self._status.check_count == 0:
            return True
        return self._status.online

    @property
    def status(self) -> ConnectivityStatus:
        return self._status

    async def check(self) -> bool:
        now = time.monotonic()
        if (
            self._status.check_count > 0
            and (now - self._status.last_checked) < self._cache_ttl
        ):
            return self._status.online

        try:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                response = await client.get(_CHECK_URLS[0])
                latency = (time.monotonic() - start) * 1000
            ok = response.status_code == 200
        except Exception:
            ok = False
            latency = 0.0

        self._status = ConnectivityStatus(
            online=ok,
            last_checked=now,
            latency_ms=latency,
            check_count=self._status.check_count + 1,
            fail_count=self._status.fail_count + (0 if ok else 1),
        )
        if ok:
            logger.debug("Internet connectivity: ONLINE (%.1fms)", latency)
        else:
            logger.debug("Internet connectivity: OFFLINE")
        return ok

    def check_background(self) -> None:
        if self._check_task is not None and not self._check_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            task: asyncio.Task[bool] = loop.create_task(self.check())
            self._check_task = task
        except RuntimeError:
            pass
