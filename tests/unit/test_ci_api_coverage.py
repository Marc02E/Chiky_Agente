"""Offline regressions for API orchestration, validation and lifecycle boundaries."""

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from personal_ai_secretary.api import app as api
from personal_ai_secretary.domain.contracts import (
    AttachedFile,
    ConversationHistory,
    ConversationMessage,
    EvidenceSourceCreate,
    ProviderInfo,
    RequestCreate,
    RequestStatus,
    SendMessageResponse,
    SessionStatus,
)
from personal_ai_secretary.providers.model_manager import ModelManager, RoutingDecision
from personal_ai_secretary.providers.model_registry import ModelStatus
from personal_ai_secretary.providers.setup import ProviderStatus
from personal_ai_secretary.shared.settings_store import ProviderSettings


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External HTTP/process execution is forbidden in these tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.fixture
def environment(monkeypatch):
    """Keep mutable settings, managers and persistence local to each test."""
    settings = api.get_settings().model_copy(deep=True)
    settings.ai_provider = "deterministic"
    settings.gemini_api_key = None
    settings.nvidia_api_key = None
    settings.opencode_server_username = None
    settings.opencode_server_password = None
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr("personal_ai_secretary.shared.config.get_settings", lambda: settings)
    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=context)
    monkeypatch.setattr(api, "get_session_factory", lambda: factory)
    store = MagicMock()
    store.load = AsyncMock(return_value=ProviderSettings())
    store.save = AsyncMock()
    monkeypatch.setattr("personal_ai_secretary.shared.settings_store.SettingsStore", lambda: store)
    health = ProviderInfo(name="ollama", mode="local", available=True, is_ai=True, detail="stub")
    provider = SimpleNamespace(
        name="ollama", model="ci-model", health=AsyncMock(return_value=health),
        list_models=AsyncMock(return_value=["ci-model", "alternate"]), warmup=AsyncMock(),
    )
    manager = ModelManager()
    manager._initialized = True
    manager._provider_instances = {"ollama": provider}
    entry = manager.registry.register("ci-model", "ollama", display_name="CI model")
    entry.status = ModelStatus.VERIFIED
    entry.measured_latency_ms = 12.345
    entry.record_success()
    entry.verified_capabilities.basic_response = True
    entry.verified_capabilities.tests_passed = 1
    entry.verified_capabilities.tests_total = 2
    manager.select_provider("ollama", "ci-model")
    monkeypatch.setattr(api, "get_model_manager", lambda: manager)
    monkeypatch.setattr(api, "get_provider", lambda: provider)
    monkeypatch.setattr(api, "get_current_model", lambda: "ci-model")
    select = MagicMock()
    monkeypatch.setattr(api, "set_current_model", select)
    return SimpleNamespace(
        settings=settings, session=session, store=store, factory=factory,
        provider=provider, manager=manager, entry=entry, select=select,
    )


@pytest.mark.parametrize("failure", [False, True])
async def test_metrics_flush_loop_recovers_and_is_cancelable(monkeypatch, failure):
    observer = SimpleNamespace(flush_metrics=AsyncMock(side_effect=RuntimeError("flush") if failure else None))
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(api.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await api._metrics_flush_loop(observer, 2.5)
    observer.flush_metrics.assert_awaited_once_with()
    assert sleep.await_args_list[0].args == (2.5,)
    assert sleep.await_count == 2


@pytest.mark.parametrize("failure", [False, True])
async def test_sweep_prunes_in_order_and_survives_database_failure(environment, monkeypatch, failure):
    from personal_ai_secretary.infrastructure import stores

    calls = []

    async def audit(session, retention):
        calls.append(("audit", session, retention))
        if failure:
            raise RuntimeError("database unavailable")

    memory = AsyncMock()
    evidence = AsyncMock()
    monkeypatch.setattr(stores, "_prune_old_audit", audit)
    monkeypatch.setattr(stores, "_prune_expired_memory", memory)
    monkeypatch.setattr(stores, "_prune_expired_evidence", evidence)
    monkeypatch.setattr(api.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await api._sweep_loop(90, 3)
    assert calls == [("audit", environment.session, 90)]
    assert memory.await_count == evidence.await_count == (0 if failure else 1)
    if not failure:
        memory.assert_awaited_once_with(environment.session)
        evidence.assert_awaited_once_with(environment.session)


async def test_sweep_disabled_does_not_open_database(environment):
    await api._sweep_loop(0, 10)
    environment.factory.assert_not_called()


@pytest.mark.parametrize("mode,models,load_failure,manager_failure", [
    ("local", ["alternate"], False, False),
    ("local", ["ci-model"], False, False),
    ("auto", [], True, False),
    ("local", None, False, True),
    ("deterministic", [], False, False),
])
async def test_lifespan_restores_settings_and_cleans_up(environment, monkeypatch, mode, models, load_failure, manager_failure):
    from personal_ai_secretary.providers import opencode_server

    e = environment
    e.settings.ai_provider = mode
    e.settings.app_env = "development"
    e.settings.audit_retention_seconds = 90
    persisted = ProviderSettings(
        gemini_api_key="synthetic-gemini", nvidia_api_key="synthetic-nvidia",
        ai_provider="unknown" if mode == "deterministic" else mode,
        ollama_model="ci-model", gemini_model="ci-cloud",
        routing_mode="manual", selected_provider="ollama", selected_model="ci-model",
    )
    e.store.load.return_value = persisted
    if load_failure:
        e.store.load.side_effect = RuntimeError("settings database unavailable")
    if models is None:
        e.provider.list_models.side_effect = RuntimeError("offline discovery failure")
    else:
        e.provider.list_models.return_value = models
    hooks = {}
    for name in ("init_database", "close_database", "initialize_model_manager"):
        hooks[name] = AsyncMock()
        monkeypatch.setattr(api, name, hooks[name])
    for name in ("configure_logging", "init_memory_store", "init_observability", "init_retriever", "init_tracing", "shutdown_tracing"):
        hooks[name] = MagicMock()
        monkeypatch.setattr(api, name, hooks[name])
    observer = SimpleNamespace(flush_metrics=AsyncMock())
    monkeypatch.setattr(api, "get_observability", lambda: observer)
    service = SimpleNamespace(recover_stale_running=AsyncMock())
    constructor = MagicMock(return_value=service)
    monkeypatch.setattr(api, "RequestService", constructor)
    sweep = AsyncMock()
    monkeypatch.setattr(api, "_sweep_loop", sweep)
    stop = MagicMock()
    monkeypatch.setattr(opencode_server, "stop_managed_server", stop)
    if manager_failure:
        monkeypatch.setattr(e.manager, "set_routing_mode", MagicMock(side_effect=RuntimeError("routing restore")))
    async with api.lifespan(api.app):
        await asyncio.sleep(0)
        service.recover_stale_running.assert_awaited_once_with()
        hooks["close_database"].assert_not_awaited()
    hooks["init_database"].assert_awaited_once_with()
    hooks["close_database"].assert_awaited_once_with()
    hooks["shutdown_tracing"].assert_called_once_with()
    stop.assert_called_once_with()
    observer.flush_metrics.assert_awaited_once_with()
    sweep.assert_awaited_once_with(retention_seconds=90, interval_seconds=e.settings.cleanup_interval_seconds)
    if not load_failure:
        assert e.settings.gemini_model == "ci-cloud"
        assert e.settings.gemini_api_key == "synthetic-gemini"
        assert e.settings.nvidia_api_key == "synthetic-nvidia"
    if mode == "local" and models:
        e.select.assert_called_once_with(models[0])
        e.provider.warmup.assert_awaited_once_with()
    if load_failure:
        assert e.manager.routing_mode == "automatic"
    elif not manager_failure:
        assert e.manager.routing_mode == "manual"
    if mode == "deterministic":
        assert e.settings.ai_provider == "deterministic"


async def test_readiness_database_failure_is_degraded(environment):
    environment.session.execute.side_effect = RuntimeError("database offline")
    response = await api.ready()
    assert response.status_code == 503
    assert json.loads(response.body)["database"] == "unavailable"


async def test_provider_status_and_discovery_contracts(environment, monkeypatch):
    from personal_ai_secretary.providers import setup

    statuses = [ProviderStatus(provider="ollama", status="AVAILABLE", detail="mock", models=["ci-model"])]
    monkeypatch.setattr(setup, "provider_statuses", AsyncMock(return_value=statuses))
    assert await api.get_provider_statuses() == [statuses[0].__dict__]
    discover = AsyncMock(return_value={"models": ["ci-model"]})
    monkeypatch.setattr(setup, "discover_models", discover)
    assert await api.get_provider_models("ollama") == {"models": ["ci-model"]}
    discover.assert_awaited_once_with("ollama")
    discover.side_effect = RuntimeError("mock discovery failure")
    with pytest.raises(HTTPException, match="503") as exc:
        await api.get_provider_models("gemini")
    assert exc.value.detail == "Model discovery failed: mock discovery failure"
    with pytest.raises(HTTPException) as exc:
        await api.get_provider_models("unknown")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("failed", [False, True])
async def test_connection_test_maps_result_or_failure(environment, monkeypatch, failed):
    from personal_ai_secretary.providers import setup

    probe = AsyncMock(return_value=SimpleNamespace(provider="ollama", status="AVAILABLE", detail="mock round-trip"))
    if failed:
        probe.side_effect = TimeoutError("mock timeout")
    monkeypatch.setattr(setup, "test_connection", probe)
    result = await api.test_provider_connection({"provider": "OLLAMA"}, {})
    assert result == {"provider": "ollama", "status": "ERROR" if failed else "AVAILABLE", "detail": "mock timeout" if failed else "mock round-trip"}
    probe.assert_awaited_once_with("ollama")
    with pytest.raises(HTTPException) as exc:
        await api.test_provider_connection({"provider": "invalid"}, {})
    assert exc.value.status_code == 400


@pytest.mark.parametrize("statuses,required", [
    ([], True),
    ([ProviderStatus(provider="gemini", status="AUTH_ERROR", detail="configured")], False),
    ([ProviderStatus(provider="nvidia", status="AVAILABLE", detail="configured")], False),
    ([ProviderStatus(provider="opencode", status="AVAILABLE", detail="local")], False),
])
async def test_setup_status_offline_and_persistence_failure(environment, monkeypatch, statuses, required):
    monkeypatch.setattr("personal_ai_secretary.providers.setup.provider_statuses", AsyncMock(return_value=statuses))
    environment.store.load.side_effect = RuntimeError("database offline")
    result = await api.get_setup_status()
    assert result["setup_required"] is required
    assert result["routing_configured"] is False
    assert result["providers"] == {s.provider: {"status": s.status, "detail": s.detail} for s in statuses}


async def test_setup_reports_persisted_routing_choice(environment, monkeypatch):
    monkeypatch.setattr("personal_ai_secretary.providers.setup.provider_statuses", AsyncMock(return_value=[]))
    environment.store.load.return_value.routing_mode = "manual"
    result = await api.get_setup_status()
    assert result["routing_configured"] is True
    assert result["setup_required"] is True
    environment.store.load.assert_awaited_once_with(environment.session)


async def test_provider_selection_persistence_and_validation(environment):
    e = environment
    assert await api.select_provider_model({"provider": " ollama ", "model": " ci-model "}, {}) == {"provider": "ollama", "model": "ci-model", "status": "selected"}
    e.store.save.assert_awaited_once_with(e.session, {"selected_provider": "ollama", "selected_model": "ci-model"})
    e.store.save.side_effect = RuntimeError("offline database")
    assert (await api.select_provider_model({"provider": "ollama"}, {}))["model"] is None
    for payload in ({}, {"provider": "missing"}):
        with pytest.raises(HTTPException) as exc:
            await api.select_provider_model(payload, {})
        assert exc.value.status_code == 400


async def test_verification_reports_are_serialized_and_rounded(environment, monkeypatch):
    report = SimpleNamespace(model_id="ci-model", provider="ollama", overall_passed=True, passed_count=1, total_duration_seconds=1.236, tests=[SimpleNamespace(test_name="response", passed=True, duration_seconds=0.126, detail="x" * 250)])
    verify = AsyncMock(return_value=report)
    monkeypatch.setattr(environment.manager, "verify_model", verify)
    monkeypatch.setattr(environment.manager, "verify_all", AsyncMock(return_value={"ollama:ci-model": report}))
    result = await api.verify_model({"provider": " ollama ", "model": " ci-model "}, {})
    assert result["duration_seconds"] == 1.24
    assert result["tests"] == [{"name": "response", "passed": True, "duration": 0.13, "detail": "x" * 200}]
    assert result["passed_count"] == result["total_tests"] == 1
    verify.assert_awaited_once_with("ollama", "ci-model")
    assert (await api.verify_all_models({}))["results"] == {"ollama:ci-model": {"passed": True, "passed_count": 1, "total_tests": 1, "duration_seconds": 1.24}}
    with pytest.raises(HTTPException) as exc:
        await api.verify_model({"provider": "ollama"}, {})
    assert exc.value.status_code == 400


async def test_recommendation_connectivity_and_model_listing(environment, monkeypatch):
    manager = environment.manager
    manager.connectivity._status.latency_ms = 12.345
    manager.connectivity._status.check_count = 4
    manager.connectivity._status.fail_count = 2
    monkeypatch.setattr(manager, "get_recommended", lambda: None)
    assert await api.get_recommended_model() == {"recommended": None, "reason": "No verified models available"}
    monkeypatch.setattr(manager, "get_recommended", lambda: {"model": "ci-model"})
    assert (await api.get_recommended_model())["recommended"] == {"model": "ci-model"}
    result = await api.check_connectivity()
    assert result == {"online": manager.connectivity.is_online, "latency_ms": 12.3, "check_count": 4, "fail_count": 2}
    listed = await api.list_all_providers_models()
    assert listed["providers"] == [environment.entry.to_dict()]
    assert listed["selected_model"] == "ci-model"


@pytest.mark.parametrize("chosen", [False, True])
async def test_auto_select_applies_task_decision(environment, monkeypatch, chosen):
    select = MagicMock(return_value=RoutingDecision("ollama", "ci-model", "vision supported") if chosen else None)
    monkeypatch.setattr(environment.manager, "select_for_task", select)
    result = await api.auto_select_model({"task_description": "describe image", "needs_vision": True})
    select.assert_called_once_with("describe image", needs_vision=True)
    assert result["selected"] is chosen
    assert result["provider"] == ("ollama" if chosen else None)
    assert result["reason"] == ("vision supported" if chosen else "No suitable model found")


@pytest.mark.parametrize("outcome", ["success", "failure", "invalid", ""])
async def test_provider_feedback_validation(environment, monkeypatch, outcome):
    success, failure = MagicMock(), MagicMock()
    monkeypatch.setattr(environment.manager, "record_success", success)
    monkeypatch.setattr(environment.manager, "record_failure", failure)
    if outcome in ("success", "failure"):
        assert await api.record_provider_feedback({"provider": "ollama", "model": "ci-model", "outcome": outcome}) == {"status": "recorded"}
        (success if outcome == "success" else failure).assert_called_once_with("ollama", "ci-model")
        (failure if outcome == "success" else success).assert_not_called()
    else:
        with pytest.raises(HTTPException) as exc:
            await api.record_provider_feedback({"provider": "ollama", "model": "ci-model", "outcome": outcome})
        assert exc.value.status_code == 400
        success.assert_not_called()
        failure.assert_not_called()


@pytest.mark.parametrize("health_failure", [False, True])
async def test_transparency_and_current_info_include_unavailable_discovery(environment, monkeypatch, health_failure):
    e = environment
    duplicate = SimpleNamespace(name="ollama", display_name="duplicate", available=True, detail="duplicate")
    missing = SimpleNamespace(name="opencode", display_name="OpenCode", available=False, detail="offline")
    monkeypatch.setattr(e.manager, "discover_providers_sync", lambda: {"ollama": duplicate, "opencode": missing})
    if health_failure:
        e.provider.health.side_effect = RuntimeError("health offline")
    result = await api.get_provider_transparency()
    assert result["status"] == "verified"
    assert result["latency_ms"] == 12.3
    assert result["reliability"] == 1.0
    assert result["verified_capabilities"]["score"] == 0.5
    assert len(result["all_providers"]) == 2
    assert result["all_providers"][1]["status"] == "unavailable"
    assert result["all_providers"][1]["detail"] == "offline"
    assert result["provider_health"]["available"] is (not health_failure)
    current = await api.get_current_provider_info()
    assert current["status"] == e.entry.to_dict()
    assert current["health"]["available"] is (not health_failure)
    if health_failure:
        assert current["health"]["detail"] == "health check failed"


async def test_factory_fallback_provider_info(environment, monkeypatch):
    monkeypatch.setattr(api, "get_model_manager", lambda: None)
    result = await api.get_current_provider_info()
    assert result == {"provider": "ollama", "health": environment.provider.health.return_value.model_dump(mode="json"), "model": "ci-model"}
    environment.provider.health.return_value.available = False
    transparency = await api.get_provider_transparency()
    assert transparency["status"] == "unavailable"
    assert transparency["provider"] == "ollama"


@pytest.mark.parametrize("persisted", [False, True])
async def test_provider_config_masks_keys_and_falls_back(environment, persisted):
    e = environment
    if persisted:
        e.store.load.return_value = ProviderSettings(gemini_api_key="synthetic-key", nvidia_api_key="synthetic-key", opencode_server_username="ci-user", opencode_server_password="synthetic-password", ollama_model="saved-model", routing_mode="manual")
    else:
        e.store.load.side_effect = RuntimeError("database unavailable")
    result = await api.get_provider_config()
    assert result["gemini_configured"] is persisted
    assert result["nvidia_configured"] is persisted
    assert result["opencode_username_configured"] is persisted
    assert result["opencode_password_configured"] is persisted
    assert result["routing_mode"] == ("manual" if persisted else "automatic")
    assert not any("synthetic" in str(value) for value in result.values())
    assert "gemini_api_key" not in result and "opencode_server_password" not in result


@pytest.mark.parametrize("payload,detail", [
    ({"gemini_api_key": 12}, "Invalid Gemini API key"),
    ({"nvidia_api_key": "short"}, "Invalid NVIDIA API key"),
    ({"opencode_server_username": []}, "Invalid OpenCode username"),
    ({"opencode_server_password": False}, "Invalid OpenCode password"),
    ({"ai_provider": "missing"}, "ai_provider must be auto, deterministic, local, or remote"),
    ({"routing_mode": "missing"}, "routing_mode must be automatic or manual"),
    ({}, "No valid configuration fields provided"),
])
async def test_config_rejects_invalid_fields_without_persistence(environment, payload, detail):
    with pytest.raises(HTTPException) as exc:
        await api.update_provider_config(payload, {})
    assert exc.value.status_code == 400
    assert exc.value.detail == detail
    environment.store.save.assert_not_awaited()


@pytest.mark.parametrize("save_failure", [False, True])
async def test_config_updates_isolated_settings_and_persists(environment, save_failure):
    payload = {"gemini_api_key": "synthetic-gemini", "nvidia_api_key": "synthetic-nvidia", "gemini_model": "cloud-model", "ollama_model": "local-model", "opencode_server_username": "ci-user", "opencode_server_password": "synthetic-password", "ai_provider": "auto", "routing_mode": "manual"}
    if save_failure:
        environment.store.save.side_effect = RuntimeError("database offline")
    result = await api.update_provider_config(payload, {})
    assert result["status"] == "updated"
    assert set(result["fields"].split(", ")) == set(payload)
    environment.store.save.assert_awaited_once_with(environment.session, payload)
    assert environment.manager.routing_mode == "manual"
    for key, value in payload.items():
        if key != "routing_mode":
            assert getattr(environment.settings, key) == value


async def test_empty_opencode_credentials_are_cleared_without_saving(environment):
    result = await api.update_provider_config({"opencode_server_username": "", "opencode_server_password": ""}, {})
    assert result["status"] == "updated"
    assert environment.settings.opencode_server_username is None
    assert environment.settings.opencode_server_password is None
    environment.store.save.assert_not_awaited()


async def test_service_selected_ollama_model_is_synchronized(environment, monkeypatch):
    from personal_ai_secretary.providers.ollama import OllamaProvider

    instance = OllamaProvider()
    instance.model = "old-model"
    environment.manager._provider_instances["ollama"] = instance
    constructor = MagicMock()
    monkeypatch.setattr(api, "RequestService", constructor)
    for name in ("get_memory_store", "get_retriever", "get_observability", "default_tool_registry"):
        monkeypatch.setattr(api, name, lambda: None)
    assert api._service(environment.session) is constructor.return_value
    assert constructor.call_args.args == (environment.session, instance)
    assert instance.model == "ci-model"


def message_result(session_id, content):
    request_id = uuid4()
    message = ConversationMessage(message_id=uuid4(), request_id=request_id, role="assistant", content=content, status="completed", created_at=datetime.now(UTC))
    return SendMessageResponse(session_id=session_id, request_id=request_id, status="completed", user_message=message.model_copy(update={"role": "user", "content": "hello"}), assistant_message=message, correlation_id="ci-correlation")


@pytest.mark.parametrize("content,approval,human", [
    ('__APPROVAL_REQUIRED__:{"tool_name":"execute_command"}\n\nReview operation', {"tool_name": "execute_command"}, "Review operation"),
    ('__APPROVAL_REQUIRED__:{"tool_name":"execute_command"}', {"tool_name": "execute_command"}, ""),
    ('__APPROVAL_REQUIRED__:invalid\nReview operation', None, "Review operation"),
])
async def test_send_message_separates_approval_and_text_images(environment, monkeypatch, content, approval, human):
    session_id = uuid4()
    original = message_result(session_id, content)
    service = SimpleNamespace(send_message=AsyncMock(return_value=original))
    monkeypatch.setattr(api, "_service", lambda db: service)
    payload = RequestCreate(input="hello", attached_files=[AttachedFile(name="notes.txt", content="reference notes", size=15), AttachedFile(name="image.png", content="c3ludGhldGlj", size=9, mime_type="image/png", is_base64=True), AttachedFile(name="empty.png", content="", size=0, mime_type="image/png")])
    result = await api.send_session_message(session_id, payload, "ci-key", True, {"sub": "alice"}, environment.session)
    args = service.send_message.await_args.args
    assert args[0].session_id == session_id
    assert "reference notes" in args[0].input
    assert "c3ludGhldGlj" not in args[0].input
    assert args[0].attached_files == []
    assert args[1] == "alice" and args[3:6] == ("ci-key", True, ["c3ludGhldGlj"])
    assert result.approval_request == approval
    assert result.assistant_message.content == human
    assert original.assistant_message.content == content
    assert payload.session_id is None


@pytest.mark.parametrize("error,code", [(PermissionError("forbidden"), 403), (ValueError("conflict"), 409)])
async def test_send_message_translates_service_errors(environment, monkeypatch, error, code):
    monkeypatch.setattr(api, "_service", lambda db: SimpleNamespace(send_message=AsyncMock(side_effect=error)))
    with pytest.raises(HTTPException) as exc:
        await api.send_session_message(uuid4(), RequestCreate(input="hello"), None, False, {}, environment.session)
    assert exc.value.status_code == code
    assert exc.value.detail == str(error)


async def test_create_permission_and_missing_resources(environment, monkeypatch):
    service = SimpleNamespace(create=AsyncMock(side_effect=PermissionError("forbidden")), history=AsyncMock(return_value=None), execute=AsyncMock(return_value=None), get=AsyncMock(return_value=None), get_session=AsyncMock(return_value=None))
    monkeypatch.setattr(api, "_service", lambda db: service)
    with pytest.raises(HTTPException) as exc:
        await api.create_request(RequestCreate(input="hello"), None, {}, environment.session)
    assert exc.value.status_code == 403
    for function, args, method in (
        (api.get_session_messages, (uuid4(), {}, environment.session), "history"),
        (api.execute_request, (uuid4(), False, {}, environment.session), "execute"),
        (api.get_request, (uuid4(), {}, environment.session), "get"),
        (api.get_session, (uuid4(), {}, environment.session), "get_session"),
    ):
        with pytest.raises(HTTPException) as exc:
            await function(*args)
        assert exc.value.status_code == 404
        assert exc.value.detail.endswith("not found")
        assert getattr(service, method).await_args.args[0] == args[0]


async def test_existing_resources_return_service_models_unchanged(environment, monkeypatch):
    request_id, session_id = uuid4(), uuid4()
    history = ConversationHistory(session_id=session_id, messages=[], correlation_id="ci")
    status = RequestStatus(request_id=request_id, status="completed", result="verified", correlation_id="ci")
    session = SessionStatus(session_id=session_id, request_count=2, correlation_id="ci")
    service = SimpleNamespace(history=AsyncMock(return_value=history), execute=AsyncMock(return_value=status), get=AsyncMock(return_value=status), get_session=AsyncMock(return_value=session))
    monkeypatch.setattr(api, "_service", lambda db: service)
    assert await api.get_session_messages(session_id, {"sub": "alice"}, environment.session) is history
    assert await api.execute_request(request_id, True, {"sub": "alice"}, environment.session) is status
    assert await api.get_request(request_id, {"sub": "alice"}, environment.session) is status
    assert await api.get_session(session_id, {"sub": "alice"}, environment.session) is session
    service.execute.assert_awaited_once_with(request_id, "alice", True)
    service.get.assert_awaited_once_with(request_id, "alice")


async def test_session_and_request_lists_map_records(environment, monkeypatch):
    now = datetime.now(UTC)
    session_id, request_id = uuid4(), uuid4()
    session_record = SimpleNamespace(session_id=session_id, user_id="alice", created_at=now, request_count=None)
    request_record = SimpleNamespace(request_id=request_id, session_id=session_id, status="completed", correlation_id="ci", created_at=now, updated_at=now)
    service = SimpleNamespace(list_sessions=AsyncMock(return_value=[session_record]), list_requests=AsyncMock(return_value=[request_record]))
    monkeypatch.setattr(api, "_service", lambda db: service)
    sessions = await api.list_sessions(7, {"sub": "alice"}, environment.session)
    requests = await api.list_requests(9, {"sub": "alice"}, environment.session)
    assert sessions.sessions[0].request_count == 0
    assert sessions.sessions[0].session_id == session_id
    assert requests.requests[0].request_id == request_id
    assert requests.requests[0].updated_at == now
    service.list_sessions.assert_awaited_once_with("alice", limit=7)
    service.list_requests.assert_awaited_once_with("alice", limit=9)


async def test_evidence_requires_persisted_timestamp(environment, monkeypatch):
    store = SimpleNamespace(add=AsyncMock(side_effect=lambda source: source))
    monkeypatch.setattr(api, "_evidence_store", lambda: store)
    payload = EvidenceSourceCreate(source_id="ci", uri="urn:ci", title="reference", content="evidence")
    with pytest.raises(RuntimeError, match="not persisted"):
        await api.add_evidence(payload, {"sub": "alice"})
    assert store.add.await_args.args[0].user_id == "alice"


async def test_middleware_propagates_errors_and_resets_correlation(environment):
    request = Request({"type": "http", "method": "GET", "path": "/test", "headers": [(b"x-correlation-id", b"ci-error")]})
    previous = api.correlation_id_var.get()
    call = AsyncMock(side_effect=RuntimeError("handler failure"))
    with pytest.raises(RuntimeError, match="handler failure"):
        await api.correlation_middleware(request, call)
    call.assert_awaited_once_with(request)
    assert api.correlation_id_var.get() == previous


def test_http_validation_envelope_and_correlation(environment):
    client = TestClient(api.app)
    response = client.get("/api/v1/providers/unknown/models", headers={"X-Correlation-ID": "ci-http", "X-Request-ID": "ci-request"})
    assert response.status_code == 400
    assert response.headers["X-Correlation-ID"] == "ci-http"
    assert response.json() == {"code": "HTTP_400", "message": "Unknown provider: unknown", "request_id": "ci-request", "details": {}, "retryable": False}


def test_run_passes_explicit_uvicorn_configuration(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr("uvicorn.run", run)
    api.run()
    run.assert_called_once_with("personal_ai_secretary.api.app:app", host="127.0.0.1", port=8000, reload=False)