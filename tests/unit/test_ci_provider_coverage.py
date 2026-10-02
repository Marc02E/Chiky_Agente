"""Offline provider setup, verification, discovery and routing regressions."""

from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from personal_ai_secretary.domain.contracts import ProviderResponse, RequestEnvelope
from personal_ai_secretary.providers import (
    capability_verifier,
    discovery,
    gemini,
    model_manager,
    ollama,
    opencode_provider,
    remote,
    setup,
)
from personal_ai_secretary.providers.connectivity import ConnectivityStatus
from personal_ai_secretary.providers.model_registry import ModelStatus
from personal_ai_secretary.shared.settings_store import ProviderSettings

_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def config(monkeypatch):
    """Use synthetic configuration instead of reading cached settings or .env."""
    cfg = SimpleNamespace(
        app_env="test", ai_provider="deterministic", provider_timeout_seconds=1,
        ollama_base_url="http://ollama.invalid", ollama_model="test-ollama",
        gemini_base_url="https://gemini.invalid", gemini_model="test-gemini",
        gemini_api_key="synthetic-gemini", nvidia_api_key="synthetic-nvidia",
        nvidia_base_url="https://nvidia.invalid/chat/completions", nvidia_model="test-nvidia",
        opencode_base_url="http://opencode.invalid", opencode_model="default",
        opencode_server_username="synthetic-user", opencode_server_password="synthetic-password",
    )
    for module in (discovery, model_manager, ollama, opencode_provider, remote, setup):
        monkeypatch.setattr(module, "get_settings", lambda: cfg)
    blocked = Mock(side_effect=AssertionError("unmocked external operation"))
    monkeypatch.setattr(httpx, "AsyncClient", blocked)
    monkeypatch.setattr(httpx, "get", blocked)
    for name in ("Popen", "run", "check_output"):
        monkeypatch.setattr(subprocess, name, blocked)
    monkeypatch.setattr(opencode_provider.OpenCodeProvider, "_is_reachable", staticmethod(lambda url: True))
    monkeypatch.setattr(opencode_provider.shutil, "which", lambda name: "C:/fake/opencode.exe")
    monkeypatch.setattr(model_manager.ConnectivityChecker, "check_background", Mock())
    return cfg


@pytest.fixture
def http(monkeypatch):
    def install(handler):
        calls = []

        def dispatch(request):
            calls.append(request)
            return handler(request)

        def client(*args, **kwargs):
            return _ASYNC_CLIENT(*args, transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        return calls

    return install


def successful_http(request):
    """Actual provider implementations consume these synthetic API responses."""
    path = request.url.path
    if path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "test-ollama"}]})
    if path == "/api/chat":
        return httpx.Response(200, json={"message": {"content": "OK"}})
    if path == "/config":
        return httpx.Response(200, json={})
    if path == "/config/providers":
        return httpx.Response(200, json={"providers": [{"id": "opencode", "models": {"oc-model": {}}}]})
    if path == "/session":
        return httpx.Response(200, json={"id": "s"})
    if path == "/session/s/message":
        return httpx.Response(200, json={"parts": [{"type": "text", "text": "OK"}]})
    if path == "/models":
        return httpx.Response(200, json={"data": [{"id": "models/z"}, {}, {"id": "models/a"}]})
    assert path == "/chat/completions", f"unexpected endpoint: {request.url}"
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})


@pytest.mark.parametrize("name", ["gemini", "nvidia", "ollama"])
@pytest.mark.parametrize("failure,status,detail", [
    (httpx.ConnectError("offline"), "UNAVAILABLE", "connect"),
    (httpx.ReadTimeout("late"), "ERROR", "timed out"),
    (httpx.RemoteProtocolError("broken"), "ERROR", "error"),
    (503, "UNAVAILABLE", "HTTP 503"),
])
async def test_setup_transport_status_mapping(http, name, failure, status, detail):
    def handler(request):
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure)

    calls = http(handler)
    check = {"gemini": setup._check_gemini_key, "nvidia": setup._check_nvidia_key,
             "ollama": setup._check_ollama}[name]
    result = await check()
    assert result[0] == status
    # Ollama's connection diagnostic is deliberately local-server specific.
    expected = "not running" if name == "ollama" and isinstance(failure, httpx.ConnectError) else detail
    assert expected.lower() in result[1].lower()
    assert len(calls) == 1
    assert calls[0].url.path == ("/api/tags" if name == "ollama" else "/models")
    if name != "ollama":
        assert calls[0].headers["authorization"] == f"Bearer synthetic-{name}"


async def test_setup_ollama_rejects_malformed_payload(http):
    http(lambda request: httpx.Response(200, content=b"invalid JSON"))
    assert await setup._check_ollama() == ("UNAVAILABLE", "Ollama returned an unexpected payload", [])


@pytest.mark.parametrize("code,status", [(200, "AVAILABLE"), (401, "AUTH_ERROR"), (403, "UNAVAILABLE"), (500, "UNAVAILABLE")])
async def test_setup_opencode_checks_real_provider_http(http, code, status):
    calls = http(lambda request: httpx.Response(code, json={}))
    actual, detail = await setup._check_opencode("user", "password")
    assert actual == status and detail
    assert len(calls) == 1 and calls[0].url.path == "/config"
    assert calls[0].headers["authorization"] == "Basic dXNlcjpwYXNzd29yZA=="


async def test_setup_opencode_not_installed_performs_no_http(monkeypatch):
    monkeypatch.setattr(opencode_provider.shutil, "which", lambda name: None)
    assert await setup._check_opencode("", "") == ("UNAVAILABLE", "OpenCode CLI is not installed")
    httpx.AsyncClient.assert_not_called()


@pytest.mark.parametrize("name,expected", [
    ("ollama", ["test-ollama"]), ("opencode", ["oc-model"]),
    ("gemini", ["a", "z"]), ("nvidia", ["models/a", "models/z"]), ("unknown", []),
])
async def test_setup_model_discovery_and_public_shape(http, name, expected):
    calls = http(successful_http)
    assert await setup.discover_models(name) == {"provider": name, "models": expected}
    assert bool(calls) is (name != "unknown")


@pytest.mark.parametrize("name", ["gemini", "nvidia"])
@pytest.mark.parametrize("failure", [401, "bad-json", httpx.ConnectError("offline"), "no-key"])
async def test_cloud_discovery_failure_is_empty(http, config, name, failure):
    if failure == "no-key":
        setattr(config, f"{name}_api_key", None)

    def handler(request):
        if isinstance(failure, Exception):
            raise failure
        if failure == "bad-json":
            return httpx.Response(200, content=b"bad")
        return httpx.Response(401)

    calls = http(handler)
    assert await setup._discover_models(name) == []
    assert len(calls) == (0 if failure == "no-key" else 1)


@pytest.mark.parametrize("name,model", [("ollama", "test-ollama"), ("opencode", "oc-model"),
                                      ("gemini", "test-gemini"), ("nvidia", "test-nvidia")])
async def test_probe_executes_provider_inference_offline(http, name, model):
    calls = http(successful_http)
    assert await setup._probe_provider(name, {"api_key": "synthetic-key", "username": "u", "password": "p"}) == ("AVAILABLE", model, "OK")
    posts = [call for call in calls if call.method == "POST"]
    assert posts
    payload = json.loads(posts[-1].content)
    if name == "opencode":
        assert payload["model"]["modelID"] == model
        assert "Reply with exactly: OK" in payload["parts"][0]["text"]
    else:
        assert payload["model"] == model
        assert payload["messages"][-1] == {"role": "user", "content": "Reply with exactly: OK"}


@pytest.mark.parametrize("models,configured,expected", [([], "default", "big-pickle"), (["listed"], "explicit", "explicit")])
async def test_opencode_probe_model_defaults(http, config, models, configured, expected):
    config.opencode_model = configured

    def handler(request):
        if request.url.path == "/config/providers":
            return httpx.Response(200, json={"providers": [{"id": "opencode", "models": dict.fromkeys(models, {})}]})
        if request.url.path.endswith("/message"):
            assert json.loads(request.content)["model"]["modelID"] == expected
        return successful_http(request)

    http(handler)
    assert await setup._probe_provider("opencode", {}) == ("AVAILABLE", expected, "OK")


async def test_probe_unknown_and_unconfigured_nvidia(config):
    config.nvidia_api_key = None
    assert await setup._probe_provider("nvidia", {}) == ("NOT_CONFIGURED", "", "")
    assert await setup._probe_provider("unknown", {}) == ("ERROR", "", "unknown provider")
    httpx.AsyncClient.assert_not_called()


async def test_persisted_credentials_override_only_present_values(config):
    persisted = ProviderSettings(gemini_api_key="saved", opencode_server_username="saved-user")
    assert setup._resolve_creds(persisted) == ("saved-user", "synthetic-password")
    assert await setup._providers_with_credentials(persisted) == {
        "gemini": {"api_key": "saved"}, "nvidia": {"api_key": "synthetic-nvidia"},
        "opencode": {"username": "saved-user", "password": "synthetic-password"},
    }
    config.opencode_server_username = config.opencode_server_password = None
    assert setup._resolve_creds(ProviderSettings()) == ("", "")


@pytest.mark.parametrize("loaded", [ProviderSettings(gemini_api_key="saved"), None, OSError("database down")])
async def test_load_persisted_database_boundary(monkeypatch, loaded):
    from personal_ai_secretary.infrastructure import database

    session = AsyncMock()
    load = AsyncMock(side_effect=loaded) if isinstance(loaded, Exception) else AsyncMock(return_value=loaded)
    monkeypatch.setattr(setup.SettingsStore, "load", load)
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: session)
    result = await setup._load_persisted()
    assert result == (loaded if isinstance(loaded, ProviderSettings) else ProviderSettings())
    load.assert_awaited_once_with(session.__aenter__.return_value)
    session.__aexit__.assert_awaited_once()


async def test_status_aggregation_uses_live_model_catalogues(http):
    http(successful_http)
    results = await setup.provider_statuses(ProviderSettings())
    assert [result.provider for result in results] == ["ollama", "opencode", "gemini", "nvidia"]
    assert [result.status for result in results] == ["AVAILABLE"] * 4
    assert results[2].models == ["a", "z"] and results[2].model == "a"
    assert results[3].models == ["models/a", "models/z"]


async def test_status_aggregation_preserves_other_results_on_failure(http, monkeypatch):
    http(successful_http)
    monkeypatch.setattr(setup, "_check_ollama", AsyncMock(side_effect=ValueError("bad tags")))
    results = await setup.provider_statuses(ProviderSettings())
    assert (results[0].provider, results[0].status, results[0].detail) == ("ollama", "ERROR", "bad tags")
    assert [result.status for result in results[1:]] == ["AVAILABLE"] * 3


@pytest.mark.parametrize("error,status,reply", [
    (RuntimeError("HTTP 401"), "AUTH_ERROR", "HTTP 401"),
    (RuntimeError("bad model"), "UNAVAILABLE", "bad model"),
    (TimeoutError("late"), "ERROR", "late"),
    (httpx.ReadTimeout("late"), "ERROR", "provider timed out"),
    (ValueError("bad payload"), "UNAVAILABLE", "bad payload"),
    (OSError("unexpected"), "ERROR", "unexpected"),
])
async def test_connection_error_result_includes_bounded_latency(monkeypatch, error, status, reply):
    monkeypatch.setattr(setup, "_probe_provider", AsyncMock(side_effect=error))
    monkeypatch.setattr(setup, "time", SimpleNamespace(monotonic=Mock(side_effect=[10.0, 10.125])))
    result = await setup.test_connection("gemini", ProviderSettings())
    assert (result.status, result.model, result.reply, result.detail, result.latency_ms) == (status, "", reply, "", 125)


async def test_connection_all_real_inference_and_aggregation(http, monkeypatch):
    http(successful_http)
    load = AsyncMock(return_value=ProviderSettings())
    monkeypatch.setattr(setup, "_load_persisted", load)
    results = await setup.test_connection_all()
    assert [result.provider for result in results] == list(setup._PROVIDER_NAMES)
    # Current Gemini probe requires the flat api_key argument, not nested persisted credentials.
    assert [result.status for result in results] == ["AVAILABLE", "AVAILABLE", "NOT_CONFIGURED", "AVAILABLE"]
    assert results[0].reply == "OK" and results[0].detail == "Connected — real model responded"
    assert all(result.latency_ms is not None and result.latency_ms >= 0 for result in results)
    load.assert_awaited_once_with()


async def test_connection_all_retains_order_when_one_task_raises(monkeypatch):
    monkeypatch.setattr(setup, "_load_persisted", AsyncMock(return_value=ProviderSettings()))

    async def connection(name, persisted):
        if name == "opencode":
            raise ValueError("unexpected task failure")
        return setup.ConnectionTestResult(provider=name, status="AVAILABLE", reply="OK")

    monkeypatch.setattr(setup, "test_connection", connection)
    results = await setup.test_connection_all()
    assert results[1] == setup.ConnectionTestResult(provider="opencode", status="ERROR", detail="unexpected task failure")
    assert [result.provider for result in results] == list(setup._PROVIDER_NAMES)


@pytest.mark.parametrize("outcome,detail", [
    (401, "invalid or expired"), (503, "HTTP 503"),
    (httpx.ReadTimeout("late"), "timed out"), (httpx.RemoteProtocolError("bad"), "unavailable"),
])
async def test_gemini_health_failure_details(http, outcome, detail):
    def handler(request):
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome)

    http(handler)
    result = await gemini.GeminiProvider(api_key="synthetic").health()
    assert not result.available and detail in result.detail
    assert "synthetic" not in result.detail


@pytest.mark.parametrize("outcome,error,detail", [
    (httpx.ConnectError("offline"), ConnectionError, "Cannot connect"),
    (httpx.ReadTimeout("late"), TimeoutError, "timed out"),
    (httpx.RemoteProtocolError("bad"), RuntimeError, "unexpected response"),
    (401, RuntimeError, "invalid or expired"), (429, RuntimeError, "rate limit"),
    (503, RuntimeError, "HTTP 503"), ("bad-json", RuntimeError, "unexpected response"),
    ({}, RuntimeError, "no choices"), ({"choices": [{"message": {}}]}, RuntimeError, "empty response"),
])
async def test_gemini_generation_failure_contract(http, outcome, error, detail):
    def handler(request):
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome)
        if outcome == "bad-json":
            return httpx.Response(200, content=b"invalid")
        return httpx.Response(200, json=outcome)

    http(handler)
    with pytest.raises(error, match=detail):
        await gemini.GeminiProvider(api_key="synthetic").generate(
            RequestEnvelope(user_id="test", input="hello", correlation_id="ci"))


async def test_gemini_model_setter_history_system_context_and_trace(http, monkeypatch):
    provider = gemini.GeminiProvider(api_key="synthetic", model="old", base_url="https://gemini.invalid")
    provider.model = "new"
    assert provider.model == "new"
    monkeypatch.setattr(gemini, "get_traceparent_header", lambda: "offline-trace")
    calls = http(successful_http)
    result = await provider.generate(RequestEnvelope(
        user_id="test", input="hello", correlation_id="ci", context_summary="system context",
        messages=[{"role": "assistant", "content": "previous"}]))
    assert result.text == "OK" and result.model == "new"
    assert calls[0].headers["traceparent"] == "offline-trace"
    assert json.loads(calls[0].content) == {"model": "new", "messages": [
        {"role": "system", "content": "system context"}, {"role": "assistant", "content": "previous"},
        {"role": "user", "content": "hello"}]}


async def test_discovery_populates_instances_resets_stale_entries(http):
    http(successful_http)
    providers = discovery.ProviderDiscovery()
    providers._discovered["stale"] = object()
    discovered = await providers.discover_all()
    assert list(discovered) == ["ollama", "gemini", "opencode", "nvidia"]
    assert discovered["gemini"].models == ["test-gemini"]
    assert discovered["opencode"].models == ["oc-model"]
    assert discovered["nvidia"].models == ["test-nvidia"]
    assert all(dp.available and dp.provider_instance is not None for dp in discovered.values())
    assert providers.get_provider("opencode") is discovered["opencode"]
    assert providers.get_provider("missing") is None


@pytest.mark.parametrize("default", ["configured", ""])
async def test_discovery_catalogue_exceptions_and_ollama_alternate_models(http, monkeypatch, config, default):
    config.ollama_model = "not-installed"
    config.opencode_model = default
    http(successful_http)
    monkeypatch.setattr(opencode_provider.OpenCodeProvider, "list_models", AsyncMock(side_effect=ValueError("bad catalogue")))
    providers = discovery.ProviderDiscovery()
    await providers._discover_opencode()
    assert providers.get_provider("opencode").models == ([default] if default else [])
    await providers._discover_ollama()
    assert providers.get_provider("ollama").available
    assert providers.get_provider("ollama").models == ["test-ollama"]
    monkeypatch.setattr(ollama.OllamaProvider, "list_models", AsyncMock(side_effect=ValueError("bad tags")))
    await providers._discover_ollama()
    assert not providers.get_provider("ollama").available
    assert providers.get_provider("ollama").models == []


class ScriptedCapabilityProvider:
    """Only inference is simulated; verifier, parser and filesystem tools are real."""

    def __init__(self, failed=()):
        self.failed = set(failed)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        texts = {
            "verify-basic": "CHIKY_OK", "verify-sysprompt": "I am Chiky",
            "verify-toolcall": self.tool("list_directory", {"path": "."}),
            "verify-args": self.tool("create_file", {"path": "unused", "content": "hello"}),
            "verify-create": self.tool("create_file", {"path": "unused", "content": "verification test"}),
            "verify-modify": self.tool("modify_file", {"path": "unused", "search": "old", "replacement": "new"}),
            "verify-verify": "Check that the file exists", "verify-coding": "def fibonacci(n): return []",
            "verify-multistep": "First plan, then write", "verify-security": "Cannot do that; unsafe",
            "verify-context": "Context information summary",
        }
        text = "" if request.correlation_id in self.failed else texts[request.correlation_id]
        return ProviderResponse(text=text, provider="scripted", model="ci-model")

    @staticmethod
    def tool(name, args):
        return f"@tool:{name} " + json.dumps(args)


async def test_capability_verifier_real_sandbox_file_operations():
    provider = ScriptedCapabilityProvider()
    report = await capability_verifier.CapabilityVerifier().verify(provider, "ci-model", "scripted")
    assert report.overall_passed and report.passed_count == 11 and report.failed_count == 0
    assert len(provider.requests) == 11
    assert {t.test_name for t in report.tests} == {
        "basic_response", "system_prompt_adherence", "tool_calling", "argument_compatibility",
        "file_creation", "file_modification", "verification_understanding", "coding", "multi_step",
        "security", "context_handling"}
    assert all(t.passed and t.duration_seconds >= 0 for t in report.tests)
    assert "created and verified" in report.tests[4].detail
    assert "modified and verified" in report.tests[5].detail


@pytest.mark.parametrize("operation", ["creation", "modification"])
@pytest.mark.parametrize("failure,expected", [
    ("missing-tool", "tool not found"), ("tool-error", "Tool execution error"),
    ("no-file", "File not found"), ("wrong-content", "content mismatch"),
])
async def test_capability_filesystem_rejects_unverified_operations(monkeypatch, operation, failure, expected):
    from pathlib import Path

    from personal_ai_secretary.tools import builtin

    async def handler(args):
        path = Path(args["path"])
        if failure == "tool-error":
            return {"error": "simulated tool failure"}
        if failure == "no-file":
            path.unlink(missing_ok=True)
        else:
            path.write_text("incorrect", encoding="utf-8")
        return {"ok": True}

    registry = SimpleNamespace(get=lambda name: None if failure == "missing-tool" else SimpleNamespace(handler=handler))
    monkeypatch.setattr(builtin, "default_tool_registry", lambda: registry)
    result = await getattr(capability_verifier.CapabilityVerifier(), f"_test_file_{operation}")(
        ScriptedCapabilityProvider(), "ci-model")
    if operation == "modification" and failure == "wrong-content":
        expected = "File not modified"
    assert not result.passed and expected in result.detail


async def test_verifier_timeout_and_unexpected_error_are_reported(monkeypatch):
    verifier = capability_verifier.CapabilityVerifier()

    async def _test_basic_response(provider, model):
        await asyncio.Event().wait()

    async def _test_system_prompt_adherence(provider, model):
        raise ValueError("unexpected verifier failure")

    monkeypatch.setattr(verifier, "_test_basic_response", _test_basic_response)
    monkeypatch.setattr(verifier, "_test_system_prompt_adherence", _test_system_prompt_adherence)
    monkeypatch.setattr(capability_verifier, "_VERIFY_TEST_TIMEOUT_SECONDS", 0.05)
    report = await verifier.verify(ScriptedCapabilityProvider(), "ci-model", "scripted")
    assert report.tests[0].test_name == "_test_basic_response" and "Timed out" in report.tests[0].detail
    assert report.tests[1].detail == "Exception: unexpected verifier failure"
    assert report.failed_count == 2 and report.passed_count == 9 and not report.overall_passed


@pytest.mark.parametrize("failed,status,passed", [
    ((), ModelStatus.VERIFIED, 11),
    (("verify-basic", "verify-sysprompt", "verify-toolcall", "verify-args", "verify-coding"), ModelStatus.LIMITED, 6),
    (("verify-basic", "verify-sysprompt", "verify-toolcall", "verify-args", "verify-create", "verify-modify"), ModelStatus.FAILED, 5),
])
async def test_manager_verification_updates_actual_capabilities(monkeypatch, failed, status, passed):
    manager = model_manager.ModelManager()
    manager._initialized = True
    manager._provider_instances["scripted"] = ScriptedCapabilityProvider(failed)
    monkeypatch.setattr(model_manager, "time", SimpleNamespace(time=lambda: 1234.0))
    report = await manager.verify_model("scripted", "ci-model")
    entry = manager.registry.get_key("ci-model", "scripted")
    assert report.passed_count == passed and entry.status == status
    assert entry.last_verified == 1234.0
    assert entry.measured_latency_ms == report.total_duration_seconds * 1000
    caps = entry.verified_capabilities
    assert (caps.tests_passed, caps.tests_total) == (passed, 11)
    names = ["basic_response", "system_prompt", "tool_calling", "argument_compatibility",
             "file_creation", "file_modification", "verification", "coding", "multi_step", "security", "context_handling"]
    for name, test in zip(names, report.tests, strict=True):
        assert getattr(caps, name) is test.passed


async def test_manager_verify_all_records_unavailable_provider():
    manager = model_manager.ModelManager()
    manager._initialized = True
    manager.registry.register("absent-model", "absent")
    report = await manager.verify_all()
    assert list(report) == ["absent:absent-model"]
    assert report["absent:absent-model"].tests == []
    assert manager.get_model_status("absent", "absent-model")["status"] == "unavailable"
    assert manager.get_model_status("absent", "missing") == {"status": "not_found"}


async def test_manager_initialization_discovers_instances_without_connectivity_thread(http):
    http(successful_http)
    manager = model_manager.ModelManager()
    providers = await manager.discover_providers()
    assert manager.discover_providers_sync() is providers
    assert manager.connectivity is manager._connectivity
    assert manager.registry.count == 4
    assert manager.get_provider_instance("gemini") is providers["gemini"].provider_instance
    assert manager.select_provider("opencode", "missing") is False
    assert manager.select_provider("opencode", "oc-model") is True
    assert manager.selected_model == "oc-model"
    assert manager.get_provider_instance().model == "oc-model"
    manager.set_routing_mode("manual")
    assert manager.routing_mode == "manual"
    manager.set_routing_mode("invalid")
    assert manager.routing_mode == "manual"
    assert manager.get_provider_instance("missing") is None
    manager.connectivity.check_background.assert_called_once_with()


@pytest.mark.parametrize("mode,available,expected", [
    ("local", ["ollama", "gemini"], "ollama"), ("local", ["gemini"], None),
    ("remote", ["opencode", "nvidia"], "nvidia"), ("remote", ["opencode"], "opencode"),
    ("remote", [], None), ("auto", ["gemini", "ollama"], "ollama"),
    ("auto", ["nvidia"], "nvidia"), ("auto", [], "deterministic"),
    ("deterministic", [], "deterministic"), ("unknown", [], None),
])
def test_manager_auto_selection_priority_and_missing_explicit_fallback(config, mode, available, expected):
    config.ai_provider = mode
    manager = model_manager.ModelManager()
    manager._provider_instances = {name: SimpleNamespace(name=name) for name in available}
    selected = manager.get_provider_instance("missing")
    assert (selected.name if selected else None) == expected


@pytest.mark.parametrize("status,latency,vision,score", [
    (ModelStatus.SLOW, 1000, True, 5 + 8 + 5 + 3 + 3 + 2),
    (ModelStatus.LIMITED, 10000, False, 3 + 8 - 5 + 3 + 1 + 2),
    (ModelStatus.UNKNOWN, 61000, True, 2 + 8 + 5 + 3 - 2 + 2),
    (ModelStatus.FAILED, 0, True, 0),
])
def test_manager_scoring_status_vision_latency_and_cloud_bonus(status, latency, vision, score):
    manager = model_manager.ModelManager()
    entry = manager.registry.register("ci-model", "gemini", coding_strength=4, supports_vision=vision)
    entry.status, entry.measured_latency_ms = status, latency
    entry.record_success()
    assert manager._score_model(entry, True, True, True) == score
    reason = manager._routing_reason(entry, True, True, False)
    assert f"Status={status.value}" in reason and "Coding=4/5" in reason and "Offline mode" in reason
    assert f"Vision={'yes' if vision else 'no'}" in reason
    if latency:
        assert f"Latency={latency}ms" in reason


def test_manager_candidates_recommendation_and_zero_score():
    manager = model_manager.ModelManager()
    manager._connectivity._status = ConnectivityStatus(online=True, check_count=1)
    cloud = manager.registry.register("cloud", "gemini")
    local = manager.registry.register("local", "ollama")
    unavailable = manager.registry.register("down", "opencode")
    unavailable.status = ModelStatus.UNAVAILABLE
    assert manager._get_candidates(True, True) == [cloud, local]
    cloud.status = ModelStatus.VERIFIED
    assert manager._get_candidates(True, True) == [cloud]
    assert manager._get_candidates(False, True) == [local]
    assert manager.get_recommended()["model_id"] == "cloud"
    cloud.status = local.status = ModelStatus.FAILED
    assert manager.select_for_task("hello") is None


def test_manager_fallback_history_skips_recent_and_limited_entries():
    manager = model_manager.ModelManager()
    manager._connectivity._status = ConnectivityStatus(online=False, check_count=1)
    limited = manager.registry.register("limited", "ollama")
    limited.status = ModelStatus.LIMITED
    recent = manager.registry.register("recent", "ollama")
    alternative = manager.registry.register("alternative", "opencode")
    alternative.status = ModelStatus.SLOW
    manager._fallback_history = ["old:model"] * 55 + ["ollama:recent"]
    assert manager.get_fallback_provider("ollama", "failed") == ("opencode", "alternative")
    assert len(manager._fallback_history) == 50
    assert manager.get_fallback_provider("opencode", "alternative", {"ollama"}) is None
    assert recent.status == ModelStatus.UNKNOWN
    manager.record_success("missing", "missing")
    manager.record_failure("missing", "missing")