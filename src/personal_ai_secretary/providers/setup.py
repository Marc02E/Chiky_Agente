"""FASE AB.6 — First-run setup service for real provider configuration.

Every function in this module talks to the REAL providers over HTTP. Nothing
here may return "AVAILABLE" merely because a key exists locally — a provider
is only AVAILABLE when it answered a live request.

Status vocabulary (FASE AB.6 spec):
  AVAILABLE       reachable and usable (validated with a real call)
  NOT_CONFIGURED  no credentials configured yet
  UNAVAILABLE     reachability failed / not running / not installed
  AUTH_ERROR      server reachable but credentials rejected (401/403)
  ERROR           unexpected failure or timeout
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import RequestEnvelope
from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.shared.settings_store import (
    ProviderSettings,
    SettingsStore,
)

_PROVIDER_NAMES: tuple[str, ...] = ("ollama", "opencode", "gemini", "nvidia")

_CONNECT_TIMEOUT = 5.0

_STATUS_AVAILABLE = "AVAILABLE"
_STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"
_STATUS_UNAVAILABLE = "UNAVAILABLE"
_STATUS_AUTH_ERROR = "AUTH_ERROR"
_STATUS_ERROR = "ERROR"


@dataclass
class ProviderStatus:
    provider: str
    status: str
    detail: str = ""
    model: str = ""
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)


@dataclass
class ConnectionTestResult:
    provider: str
    status: str
    detail: str = ""
    model: str = ""
    latency_ms: int | None = None
    reply: str = ""


async def _load_persisted() -> ProviderSettings:
    """Load provider settings from DB, tolerating an unavailable DB."""
    try:
        store = SettingsStore()
        from personal_ai_secretary.infrastructure.database import get_session_factory

        async with get_session_factory()() as session:
            settings = await store.load(session)
            if settings is not None:
                return settings
    except Exception:
        pass
    return ProviderSettings()


def _resolve_creds(persisted: ProviderSettings) -> tuple[str, str]:
    """Return (opencode_username, opencode_password) honoring persisted + env."""
    cfg = get_settings()
    oc_user = (
        persisted.opencode_server_username
        or cfg.opencode_server_username
        or ""
    )
    oc_pass = (
        persisted.opencode_server_password
        or cfg.opencode_server_password
        or ""
    )
    return oc_user, oc_pass


def _build_envelope(prompt: str = "Reply with exactly: OK") -> RequestEnvelope:
    from uuid import uuid4

    return RequestEnvelope(
        user_id="system",
        input=prompt,
        correlation_id=str(uuid4()),
        session_id=uuid4(),
    )


async def _check_gemini_key() -> tuple[str, str]:
    """Real connectivity check for Gemini. Returns (status, detail)."""
    cfg = get_settings()
    key = cfg.gemini_api_key or ""
    if not key:
        return _STATUS_NOT_CONFIGURED, "Gemini API key not configured"
    try:
        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
            response = await client.get(
                f"{cfg.gemini_base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.ConnectError:
        return _STATUS_UNAVAILABLE, "Cannot connect to Gemini API"
    except httpx.TimeoutException:
        return _STATUS_ERROR, "Gemini API connection timed out"
    except httpx.HTTPError as exc:
        return _STATUS_ERROR, f"Gemini API error: {exc}"
    if response.status_code == 200:
        return _STATUS_AVAILABLE, "Gemini API reachable"
    if response.status_code in (401, 403):
        return _STATUS_AUTH_ERROR, "Gemini API key invalid or expired"
    return _STATUS_UNAVAILABLE, f"Gemini API answered HTTP {response.status_code}"


async def _check_nvidia_key() -> tuple[str, str]:
    """Real connectivity check for NVIDIA (actual network call, not key check)."""
    cfg = get_settings()
    key = cfg.nvidia_api_key or ""
    if not key:
        return _STATUS_NOT_CONFIGURED, "NVIDIA API key not configured"
    # The NVIDIA endpoint is the chat completions URL; use a zero-token probe.
    probe_url = cfg.nvidia_base_url.replace("/chat/completions", "") + "/models"
    try:
        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
            response = await client.get(
                probe_url,
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.ConnectError:
        return _STATUS_UNAVAILABLE, "Cannot connect to NVIDIA API"
    except httpx.TimeoutException:
        return _STATUS_ERROR, "NVIDIA API connection timed out"
    except httpx.HTTPError as exc:
        return _STATUS_ERROR, f"NVIDIA API error: {exc}"
    if response.status_code == 200:
        return _STATUS_AVAILABLE, "NVIDIA API reachable"
    if response.status_code in (401, 403):
        return _STATUS_AUTH_ERROR, "NVIDIA API key invalid or expired"
    return _STATUS_UNAVAILABLE, f"NVIDIA API answered HTTP {response.status_code}"


async def _check_ollama() -> tuple[str, str, list[str]]:
    cfg = get_settings()
    try:
        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
            response = await client.get(f"{cfg.ollama_base_url}/api/tags")
    except httpx.ConnectError:
        return _STATUS_UNAVAILABLE, "Ollama is not running", []
    except httpx.TimeoutException:
        return _STATUS_ERROR, "Ollama connection timed out", []
    except httpx.HTTPError as exc:
        return _STATUS_ERROR, f"Ollama error: {exc}", []
    if response.status_code != 200:
        return _STATUS_UNAVAILABLE, f"Ollama answered HTTP {response.status_code}", []
    try:
        models = response.json().get("models", [])
        ids = sorted(str(m.get("name", "")) for m in models if m.get("name"))
    except ValueError:
        return _STATUS_UNAVAILABLE, "Ollama returned an unexpected payload", []
    if not ids:
        return _STATUS_UNAVAILABLE, "Ollama is running but has no models installed", []
    return _STATUS_AVAILABLE, f"Ollama reachable with {len(ids)} models", ids


async def _check_opencode(oc_user: str, oc_pass: str) -> tuple[str, str]:
    from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

    provider = OpenCodeProvider(
        username=oc_user or None,
        password=oc_pass or None,
        manage_server=True,
    )
    installed = provider.is_installed()
    if not installed:
        return _STATUS_UNAVAILABLE, "OpenCode CLI is not installed"
    ok, detail = await provider._check_server()
    if ok:
        return _STATUS_AVAILABLE, f"OpenCode server running at {provider._base_url}"
    if "authentication" in detail.lower() or "credentials" in detail.lower():
        return _STATUS_AUTH_ERROR, detail
    return _STATUS_UNAVAILABLE, f"OpenCode is installed but {detail}"


async def provider_statuses(persisted: ProviderSettings | None = None) -> list[ProviderStatus]:
    """Return real, live status for every provider."""
    persisted = persisted or await _load_persisted()

    async def _ollama() -> ProviderStatus:
        status, detail, models = await _check_ollama()
        return ProviderStatus(
            provider="ollama",
            status=status,
            detail=detail,
            model=models[0] if models else "",
            models=models,
        )

    async def _opencode() -> ProviderStatus:
        oc_user, oc_pass = _resolve_creds(persisted)
        status, detail = await _check_opencode(oc_user, oc_pass)
        return ProviderStatus(provider="opencode", status=status, detail=detail)

    async def _gemini() -> ProviderStatus:
        status, detail = await _check_gemini_key()
        models = await _discover_models("gemini") if status == _STATUS_AVAILABLE else []
        return ProviderStatus(
            provider="gemini",
            status=status,
            detail=detail,
            model=models[0] if models else "",
            models=models,
        )

    async def _nvidia() -> ProviderStatus:
        status, detail = await _check_nvidia_key()
        models = await _discover_models("nvidia") if status == _STATUS_AVAILABLE else []
        return ProviderStatus(
            provider="nvidia",
            status=status,
            detail=detail,
            model=models[0] if models else "",
            models=models,
        )

    results = await asyncio.gather(
        _ollama(), _opencode(), _gemini(), _nvidia(), return_exceptions=True
    )
    out: list[ProviderStatus] = []
    for name, result in zip(_PROVIDER_NAMES, results, strict=False):
        if isinstance(result, Exception):
            out.append(ProviderStatus(provider=name, status=_STATUS_ERROR, detail=str(result)))
        elif isinstance(result, ProviderStatus):
            out.append(result)
    return out


async def _discover_models(provider_name: str) -> list[str]:
    """Real model discovery — never a static list."""
    cfg = get_settings()
    try:
        if provider_name == "ollama":
            _, _, models = await _check_ollama()
            return models
        if provider_name == "opencode":
            from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

            p = OpenCodeProvider(manage_server=True)
            return await p.list_models()
        if provider_name == "gemini":
            key = cfg.gemini_api_key or ""
            if not key:
                return []
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(
                    f"{cfg.gemini_base_url}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if response.status_code != 200:
                return []
            data = response.json()
            return sorted(
                str(m.get("id", "")).replace("models/", "")
                for m in data.get("data", [])
                if m.get("id")
            )
        if provider_name == "nvidia":
            key = cfg.nvidia_api_key or ""
            if not key:
                return []
            probe_url = cfg.nvidia_base_url.replace("/chat/completions", "") + "/models"
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(
                    probe_url, headers={"Authorization": f"Bearer {key}"}
                )
            if response.status_code != 200:
                return []
            data = response.json()
            return sorted(str(m.get("id", "")) for m in data.get("data", []) if m.get("id"))
    except Exception:
        return []
    return []


async def discover_models(provider_name: str) -> dict[str, Any]:
    """Public discovery API: live models for one provider."""
    return {
        "provider": provider_name,
        "models": await _discover_models(provider_name),
    }


async def _providers_with_credentials(persisted: ProviderSettings) -> dict[str, Any]:
    cfg = get_settings()
    gemini_key = persisted.gemini_api_key or cfg.gemini_api_key or ""
    nvidia_key = persisted.nvidia_api_key or cfg.nvidia_api_key or ""
    oc_user = persisted.opencode_server_username or cfg.opencode_server_username or ""
    oc_pass = persisted.opencode_server_password or cfg.opencode_server_password or ""

    return {
        "gemini": {"api_key": gemini_key},
        "nvidia": {"api_key": nvidia_key},
        "opencode": {"username": oc_user, "password": oc_pass},
    }


async def _probe_provider(provider_name: str, creds: dict[str, Any]) -> tuple[str, str, str]:
    """Run one real inference. Returns (status, model, reply)."""
    cfg = get_settings()
    if provider_name == "ollama":
        from personal_ai_secretary.providers.ollama import OllamaProvider

        _, _, models = await _check_ollama()
        model = models[0] if models else cfg.ollama_model
        ollama = OllamaProvider(model=model)
        ollama_response = await ollama.generate(_build_envelope())
        return _STATUS_AVAILABLE, model, ollama_response.text
    if provider_name == "opencode":
        from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

        opencode = OpenCodeProvider(
            username=creds.get("username") or None,
            password=creds.get("password") or None,
            model=cfg.opencode_model,
            manage_server=True,
        )
        if cfg.opencode_model in ("", "default"):
            discovered = await opencode.list_models()
            opencode.model = discovered[0] if discovered else "big-pickle"
        opencode_response = await opencode.generate(_build_envelope())
        return _STATUS_AVAILABLE, opencode.model, opencode_response.text
    if provider_name == "gemini":
        from personal_ai_secretary.providers.gemini import GeminiProvider

        gemini = GeminiProvider(
            api_key=creds.get("api_key"),
            model=cfg.gemini_model,
            base_url=cfg.gemini_base_url,
        )
        gemini_response = await gemini.generate(_build_envelope())
        return _STATUS_AVAILABLE, cfg.gemini_model, gemini_response.text
    if provider_name == "nvidia":
        from personal_ai_secretary.providers.remote import NVIDIAProvider

        if not cfg.nvidia_api_key and not creds.get("api_key"):
            return _STATUS_NOT_CONFIGURED, "", ""
        response = await NVIDIAProvider().generate(_build_envelope())
        return _STATUS_AVAILABLE, cfg.nvidia_model, response.text
    return _STATUS_ERROR, "", "unknown provider"


def _map_probe_exception(exc: Exception) -> tuple[str, str, str]:
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "not configured" in msg:
            return _STATUS_NOT_CONFIGURED, "", str(exc)
        if "invalid or expired" in msg or "401" in msg:
            return _STATUS_AUTH_ERROR, "", str(exc)
        return _STATUS_UNAVAILABLE, "", str(exc)
    if isinstance(exc, ConnectionError):
        return _STATUS_UNAVAILABLE, "", str(exc)
    if isinstance(exc, TimeoutError):
        return _STATUS_ERROR, "", str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return _STATUS_ERROR, "", "provider timed out"
    if isinstance(exc, ValueError):
        return _STATUS_UNAVAILABLE, "", str(exc)
    return _STATUS_ERROR, "", str(exc)


async def test_connection(
    provider_name: str, persisted: ProviderSettings | None = None
) -> ConnectionTestResult:
    """Real end-to-end connection test (actual model inference)."""
    persisted = persisted or await _load_persisted()
    creds = await _providers_with_credentials(persisted)

    started = time.monotonic()
    try:
        status, model, reply = await _probe_provider(provider_name, creds)
    except Exception as exc:  # noqa: BLE001
        status, model, reply = _map_probe_exception(exc)
    latency_ms = int((time.monotonic() - started) * 1000)
    detail = ""
    if status == _STATUS_AVAILABLE:
        detail = "Connected — real model responded"
    return ConnectionTestResult(
        provider=provider_name,
        status=status,
        detail=detail,
        model=model,
        latency_ms=latency_ms,
        reply=reply,
    )


async def test_connection_all() -> list[ConnectionTestResult]:
    """Run real connection tests against every provider concurrently."""
    persisted = await _load_persisted()
    results = await asyncio.gather(
        *(test_connection(p, persisted) for p in _PROVIDER_NAMES),
        return_exceptions=True,
    )
    out: list[ConnectionTestResult] = []
    for name, result in zip(_PROVIDER_NAMES, results, strict=False):
        if isinstance(result, Exception):
            out.append(
                ConnectionTestResult(
                    provider=name, status=_STATUS_ERROR, detail=str(result)
                )
            )
        elif isinstance(result, ConnectionTestResult):
            out.append(result)
    return out