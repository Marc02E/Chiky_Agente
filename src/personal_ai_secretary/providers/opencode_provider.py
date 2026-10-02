"""OpenCode provider integration for Chiky.

FASE T/Y: Detects OpenCode availability and routes requests through an
OpenCode server using its native (non OpenAI-compatible) HTTP API.

OpenCode (https://opencode.ai) is a CLI/desktop tool that exposes a JSON
server. The v1.x server does NOT expose /v1/chat/completions; instead the
native API is:

  * GET  /config              -> JSON (exercise as health check)
  * GET  /config/providers    -> provider/model catalogue
  * POST /session             -> create a chat session
  * POST /session/{id}/message-> send a message, returns assistant parts

Authentication is optional and controlled by the server via HTTP Basic auth
(OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD). This provider sends
those credentials automatically when Chiky runs with them configured.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import (
    ProviderInfo,
    ProviderResponse,
    RequestEnvelope,
)
from personal_ai_secretary.observability.tracing import get_traceparent_header
from personal_ai_secretary.shared.config import get_settings

logger = logging.getLogger("personal_ai_secretary.providers.opencode")

_CONNECT_TIMEOUT = 5.0
# Read timeout (s) for a single OpenCode inference call. Reduced from 600s in
# FASE AB.8: a stalled managed `opencode serve` after a restart previously kept
# the UI in "Thinking…" for up to 10 minutes with no bound. 240s (4 min) caps
# the wait while still allowing legitimate slow first-responses; the UI waits
# on this same cap, so there is no longer unbounded loading. Do not lower below
# ~120s without evidence, as cold-start responses can be slow.
_READ_TIMEOUT = 240.0
_DEFAULT_PORT = 4097


class OpenCodeProvider:
    name = "opencode"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        username: str | None = None,
        password: str | None = None,
        manage_server: bool = False,
    ) -> None:
        settings = get_settings()
        self._base_url = base_url or (
            settings.opencode_base_url or f"http://127.0.0.1:{_DEFAULT_PORT}"
        )
        self._model = model or settings.opencode_model or "default"
        # Prefer explicit credentials; fall back to server env vars.
        self._username = username or settings.opencode_server_username
        self._password = password or settings.opencode_server_password
        if not self._username:
            self._username = os.environ.get("OPENCODE_SERVER_USERNAME")
        if not self._password:
            self._password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        # FASE AB.7: allow Chiky to own an `opencode serve` process when no
        # reachable external server already provides a usable endpoint.
        self._manage_server = manage_server
        self._managed: Any = None
        self._available: bool | None = None
        self._model_provider_cache: dict[str, str] | None = None

    def _server(self) -> str:
        """Return a reachable server base URL, starting managed one if needed."""
        if self._is_reachable(self._base_url):
            return self._base_url
        if self._manage_server:
            mgr = self._ensure_managed()
            if mgr is not None and mgr.base_url:
                return str(mgr.base_url)
        return self._base_url

    def _ensure_managed(self) -> Any:
        if self._managed is not None and self._managed.running:
            return self._managed
        from personal_ai_secretary.providers.opencode_server import (
            get_managed_server,
        )

        mgr = get_managed_server()
        if self._username:
            mgr.username = self._username
        if self._password:
            mgr.password = self._password
        # FASE AB.7: never spawn OS processes during the unit-test suite.
        # An integration test may set CHIKY_ALLOW_LIVE_INTEGRATION=1 to
        # allow managed server startup even when APP_ENV=test.
        if (
            get_settings().app_env == "test"
            and not os.environ.get("CHIKY_ALLOW_LIVE_INTEGRATION")
        ):
            logger.warning(
                "App is running in test env; refusing to start managed "
                "OpenCode subprocess."
            )
            return None
        started = mgr.ensure_running()
        if started:
            self._managed = mgr
            self._base_url = mgr.base_url
        else:
            logger.warning("Managed OpenCode server failed to become ready.")
        return mgr if started else None

    @staticmethod
    def _is_reachable(url: str) -> bool:
        try:
            resp = httpx.get(f"{url}/config", timeout=_CONNECT_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value
        self._model_provider_cache = None

    @staticmethod
    def is_installed() -> bool:
        return shutil.which("opencode") is not None

    def _auth_headers(self) -> dict[str, str]:
        if self._username and self._password:
            token = base64.b64encode(
                f"{self._username}:{self._password}".encode()
            ).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        # FASE AB.7: a managed server always has a credential pair.
        if self._managed is not None:
            return self._managed._auth_headers() if hasattr(self._managed, "_auth_headers") else {}
        return {}

    async def _check_server(self) -> tuple[bool, str]:
        """Return (ok, detail). ok=True when the server answers /config JSON."""
        # FASE AB.7: if the directory server is unreachable, try to start or
        # attach to a Chiky-managed server.
        base_url = self._base_url
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(
                    f"{base_url}/config", headers=headers
                )
        except httpx.ConnectError:
            if self._manage_server:
                mgr = self._ensure_managed()
                if mgr is not None:
                    base_url = mgr.base_url
                    headers = self._auth_headers()
                    try:
                        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                            response = await client.get(
                                f"{base_url}/config", headers=headers
                            )
                    except (httpx.ConnectError, httpx.TimeoutException) as exc:
                        return False, f"managed server unreachable: {type(exc).__name__}"
                    except Exception:
                        return False, "managed server unreachable"
                else:
                    return False, "server is not running and managed start failed"
            else:
                return False, "server is not running"
        except httpx.TimeoutException:
            return False, "server did not respond in time"
        except Exception:
            return False, "unreachable"
        if response.status_code == 401:
            return False, (
                "server requires authentication but credentials are missing "
                "or invalid (set OPENCODE_SERVER_USERNAME/PASSWORD)"
            )
        if response.status_code == 200:
            try:
                response.json()
                return True, "server is running"
            except ValueError:
                return False, "server returned an unexpected payload"
        return False, f"server answered HTTP {response.status_code}"

    async def health(self) -> ProviderInfo:
        installed = self.is_installed()
        if not installed:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="OpenCode is not installed. Install from https://opencode.ai",
            )
        ok, detail = await self._check_server()
        if not ok:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail=f"OpenCode is installed but {detail}.",
            )
        # FASE AB.7: reflect the actual server endpoint that answered.
        base_url = self._managed.base_url if self._managed is not None else self._base_url
        return ProviderInfo(
            name=self.name,
            mode="local",
            available=True,
            is_ai=True,
            detail=f"OpenCode server running at {base_url}",
        )

    async def _catalogue(self) -> dict[str, list[str]]:
        """Map provider id -> list of available model ids (from /config/providers)."""
        result: dict[str, list[str]] = {}
        base_url = self._server()
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(
                    f"{base_url}/config/providers", headers=headers
                )
            if response.status_code != 200:
                return result
            data = response.json()
            for provider in data.get("providers", []):
                pid = provider.get("id") or provider.get("name") or ""
                models = provider.get("models", {})
                ids = [str(m) for m in models.keys()]
                if pid:
                    result[pid] = ids
        except Exception:
            pass
        return result

    def _find_provider_for_model(
        self, catalogue: dict[str, list[str]], model_id: str
    ) -> str:
        for pid, model_ids in catalogue.items():
            if model_id in model_ids:
                return pid
        return "opencode"

    async def list_models(self) -> list[str]:
        catalogue = await self._catalogue()
        seen: list[str] = []
        for pid, models in catalogue.items():
            if pid == "opencode":
                for m in models:
                    if m not in seen:
                        seen.append(m)
        if not seen:
            # Fall back to whatever the server reports (any provider).
            for models in catalogue.values():
                for m in models:
                    if m not in seen:
                        seen.append(m)
        if not seen and self._model != "default":
            seen.append(self._model)
        return seen

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        text_prompt = self._build_prompt(request)
        headers = self._auth_headers()
        traceparent = get_traceparent_header()
        if traceparent:
            headers["traceparent"] = traceparent
        headers["Content-Type"] = "application/json"

        try:
            base_url = self._server()
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
            ) as client:
                # 1) Create a throwaway session.
                create_resp = await client.post(
                    f"{base_url}/session",
                    headers={**headers, "Content-Type": "application/json"},
                    json={},
                )
                create_resp.raise_for_status()
                session_id = create_resp.json().get("id")
                if not session_id:
                    raise RuntimeError("OpenCode server returned no session id.")

                # 2) Resolve the provider that actually serves the selected model.
                catalogue = await self._catalogue()
                provider_id = self._find_provider_for_model(
                    catalogue, self._model
                )
                model_info = {
                    "id": self._model,
                    "providerID": provider_id,
                    "modelID": self._model,
                }
                payload: dict[str, Any] = {
                    "providerId": provider_id,
                    "model": model_info,
                    "parts": [{"type": "text", "text": text_prompt}],
                }
                msg_resp = await client.post(
                    f"{base_url}/session/{session_id}/message",
                    headers=headers,
                    json=payload,
                )
                msg_resp.raise_for_status()
                data = msg_resp.json()
        except httpx.ConnectError as exc:
            raise ConnectionError(
                "Cannot connect to OpenCode server. Ensure it is running."
            ) from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError("OpenCode server timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"OpenCode server error (HTTP {exc.response.status_code})."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("OpenCode returned an unexpected response.") from exc

        text = self._extract_text(data)
        if not text:
            raise RuntimeError("OpenCode returned an empty response.")
        return ProviderResponse(
            text=text,
            provider=self.name,
            model=self._model,
            raw=data,
        )

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """Aggregate 'text' parts from an assistant message response."""
        parts = data.get("parts", []) if isinstance(data, dict) else []
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                chunk = part.get("text")
                if isinstance(chunk, str) and chunk:
                    chunks.append(chunk)
        return "\n".join(chunks).strip()

    @staticmethod
    def _build_prompt(request: RequestEnvelope) -> str:
        """Pack system context, history, and the user prompt into one message."""
        chunks: list[str] = []
        if request.context_summary:
            chunks.append(f"<system>\n{request.context_summary}\n</system>")
        for turn in request.messages:
            chunks.append(f"<{turn.role}>\n{turn.content}\n</{turn.role}>")
        chunks.append(f"<user>\n{request.input}\n</user>")
        return "\n\n".join(chunks)