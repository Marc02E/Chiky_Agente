"""FASE RELEASE (corrección quirúrgica): MANUAL sin proveedor disponible.

Cierra el camino latente de `api.app._service()`: cuando el routing es MANUAL
y el proveedor seleccionado no tiene instancia disponible, el sistema DEBÍA
haber fallado explícitamente con provenance, pero antes caía silenciosamente
a `get_provider()` (el proveedor configurado por defecto), ejecutando la
petición con un proveedor equivocado sin trazabilidad.

Determinístico y sin servidores reales: se inyecta un ModelManager falso cuyo
`get_provider_instance()` siempre devuelve None.

Contractos verificados:
  - FALLO EXPLÍCITO: la petición queda en status `failed`, con un mensaje
    comprensible en la respuesta (200) y en el registro.
  - SIN FALLBACK SILENCIOSO: `fallback_active = False`, `fallback_chain == []`,
    `executed_provider is None` (NADIE ejecutó con un proveedor equivocado).
  - PROVENANCE: `fallback_info.status == "unavailable"` con requested/selected/
    attempted = opencode / big-pickle (el target manual).
  - AUTOMATIC intacto: con instancia ausente en AUTOMATIC se conserva el
    provider configurado como defensivo (no se rompe el modo automático).
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.providers.model_manager import ModelManager


class _NoopRegistry:
    def get_key(self, model_id: str, provider_name: str):  # type: ignore[no-untyped-def]  # noqa: ARG002
        return None


class _AbsentProviderManager(ModelManager):
    """ModelManager sin ninguna instancia de proveedor disponible."""

    _initialized: bool = True
    _routing_mode: str = "automatic"
    _selected_provider: str | None = "opencode"
    _selected_model: str | None = "big-pickle"

    def __init__(self) -> None:
        self._registry = _NoopRegistry()

    @property
    def registry(self) -> _NoopRegistry:  # type: ignore[override]
        return self._registry

    def get_provider_instance(self, name: str | None = None) -> None:  # type: ignore[override]
        return None

    def get_all_status(self) -> list[dict[str, object]]:  # pragma: no cover
        return []


def test_manual_unavailable_no_silent_fallback_end_to_end() -> None:
    """MANUAL + instancia ausente => error explícito con provenance, sin ejecutar otro provider."""
    manager = _AbsentProviderManager()
    manager._routing_mode = "manual"
    with TestClient(app) as client, patch(
        "personal_ai_secretary.api.app.get_model_manager", return_value=manager
    ), patch(
        "personal_ai_secretary.providers.factory.get_model_manager", return_value=manager
    ):
        resp = client.post(
            "/api/v1/sessions/00000000-0000-0000-0000-00000000aa01/messages",
            headers={"Authorization": "Bearer test-token"},
            json={"input": "manual sin proveedor"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    content = body["assistant_message"]["content"] or ""
    assert "no automatic fallback is allowed" in content

    fb = body["fallback_info"]
    assert fb is not None, "provenance must be present even when nothing executed"
    assert fb["fallback_active"] is False
    assert fb["fallback_chain"] == []
    assert fb["requested_provider"] == "opencode"
    assert fb["requested_model"] == "big-pickle"
    assert fb["selected_provider"] == "opencode"
    assert fb["attempted_provider"] == "opencode"
    assert fb["executed_provider"] is None, "no provider may have executed silently"
    assert fb["executed_model"] is None
    assert fb["status"] == "unavailable"


def test_service_refuses_silent_fallback_manual_unavailable(monkeypatch) -> None:
    """`_service()` MANUAL sin instancia deja provider=None (no sustituye)."""
    from personal_ai_secretary.api.app import _service

    manager = _AbsentProviderManager()
    manager._routing_mode = "manual"
    marker = object()
    monkeypatch.setattr(
        "personal_ai_secretary.api.app.get_model_manager", lambda: manager
    )
    monkeypatch.setattr("personal_ai_secretary.api.app.get_provider", lambda: marker)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_memory_store", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_retriever", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.default_tool_registry", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_observability", lambda: None)

    service = _service(object())  # type: ignore[arg-type]

    assert service.provider is None, "provider must not fall back to get_provider()"
    assert service._last_fallback is not None
    assert service._last_fallback["status"] == "unavailable"
    assert service._last_fallback["requested_provider"] == "opencode"
    assert service._last_fallback["requested_model"] == "big-pickle"
    assert service._last_fallback["executed_provider"] is None
    assert service._last_fallback["fallback_active"] is False


def test_service_keeps_defensive_provider_in_automatic(monkeypatch) -> None:
    """AUTOMATIC + instancia ausente conserva el provider configurado (sin romper el modo)."""
    from personal_ai_secretary.api.app import _service

    manager = _AbsentProviderManager()
    manager._routing_mode = "automatic"
    marker = object()
    monkeypatch.setattr(
        "personal_ai_secretary.api.app.get_model_manager", lambda: manager
    )
    monkeypatch.setattr("personal_ai_secretary.api.app.get_provider", lambda: marker)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_memory_store", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_retriever", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.default_tool_registry", lambda: None)
    monkeypatch.setattr("personal_ai_secretary.api.app.get_observability", lambda: None)

    service = _service(object())  # type: ignore[arg-type]

    assert service.provider is marker, "automatic defensive fallback must be preserved"
    assert service._last_fallback is None