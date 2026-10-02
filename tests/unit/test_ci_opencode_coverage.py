"""Offline OpenCode protocol and lifecycle contracts, with real module execution."""

from __future__ import annotations

import base64
import ctypes
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from personal_ai_secretary.domain.contracts import RequestEnvelope
from personal_ai_secretary.providers import opencode_provider as provider_module
from personal_ai_secretary.providers import opencode_server as server_module

_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No environment credentials, HTTP sockets, subprocesses or native jobs."""
    cfg = SimpleNamespace(
        app_env="test", opencode_base_url="http://opencode.invalid",
        opencode_model="default", opencode_server_username=None,
        opencode_server_password=None,
    )
    monkeypatch.setattr(provider_module, "get_settings", lambda: cfg)
    for key in ("OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD",
                "CHIKY_OPENCODE_USERNAME", "CHIKY_OPENCODE_PASSWORD",
                "CHIKY_ALLOW_LIVE_INTEGRATION"):
        monkeypatch.delenv(key, raising=False)
    blocked = Mock(side_effect=AssertionError("unmocked external operation"))
    monkeypatch.setattr(httpx, "get", blocked)
    monkeypatch.setattr(httpx, "AsyncClient", blocked)
    for name in ("Popen", "run", "check_output"):
        monkeypatch.setattr(subprocess, name, blocked)
    monkeypatch.setattr(ctypes, "WinDLL", blocked, raising=False)
    monkeypatch.setattr(server_module, "_managed_server", None)
    return cfg


@pytest.fixture
def http(monkeypatch):
    calls = []

    def install(handler):
        def dispatch(request):
            calls.append(request)
            return handler(request)

        def client(*args, **kwargs):
            return _ASYNC_CLIENT(*args, transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        return calls

    return install


@pytest.mark.parametrize("outcome,expected", [
    (200, (True, "server is running")),
    (401, (False, "authentication")),
    (503, (False, "server answered HTTP 503")),
    ("bad-json", (False, "unexpected payload")),
    (httpx.ConnectError("offline"), (False, "server is not running")),
    (httpx.ReadTimeout("late"), (False, "server did not respond in time")),
    (ValueError("bad"), (False, "unreachable")),
])
async def test_server_check_status_and_transport_errors(http, outcome, expected):
    def handler(request):
        assert request.url.path == "/config"
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "bad-json":
            return httpx.Response(200, content=b"not JSON")
        return httpx.Response(outcome, json={})

    http(handler)
    ok, detail = await provider_module.OpenCodeProvider()._check_server()
    assert ok is expected[0]
    assert expected[1] in detail


@pytest.mark.parametrize("outcome,expected", [
    (200, (True, "server is running")),
    (httpx.ConnectError("offline"), (False, "managed server unreachable: ConnectError")),
    (httpx.ReadTimeout("late"), (False, "managed server unreachable: ReadTimeout")),
    (ValueError("bad"), (False, "managed server unreachable")),
    (None, (False, "server is not running and managed start failed")),
])
async def test_check_server_managed_retry(http, monkeypatch, outcome, expected):
    provider = provider_module.OpenCodeProvider(manage_server=True)
    managed = SimpleNamespace(base_url="http://managed.invalid")
    ensure = Mock(return_value=managed if outcome is not None else None)
    monkeypatch.setattr(provider, "_ensure_managed", ensure)

    def handler(request):
        if request.url.host == "opencode.invalid":
            raise httpx.ConnectError("external down")
        assert request.url.host == "managed.invalid"
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(200, json={})

    calls = http(handler)
    assert await provider._check_server() == expected
    ensure.assert_called_once_with()
    assert len(calls) == (1 if outcome is None else 2)


def test_managed_start_guard_and_credential_transfer(monkeypatch, offline):
    provider = provider_module.OpenCodeProvider(username="test-user", password="test-password")
    managed = SimpleNamespace(running=False, base_url="http://managed.invalid",
                              ensure_running=Mock(return_value=True))
    monkeypatch.setattr(server_module, "get_managed_server", lambda: managed)
    assert provider._ensure_managed() is None
    managed.ensure_running.assert_not_called()
    assert (managed.username, managed.password) == ("test-user", "test-password")
    offline.app_env = "production"
    assert provider._ensure_managed() is managed
    assert provider._base_url == managed.base_url
    managed.running = True
    assert provider._ensure_managed() is managed
    managed.ensure_running.assert_called_once_with()


def test_managed_failure_and_server_resolution(monkeypatch, offline):
    offline.app_env = "production"
    managed = SimpleNamespace(base_url="http://managed.invalid", ensure_running=Mock(return_value=False))
    monkeypatch.setattr(server_module, "get_managed_server", lambda: managed)
    provider = provider_module.OpenCodeProvider(manage_server=True)
    assert provider._ensure_managed() is None
    monkeypatch.setattr(provider, "_is_reachable", lambda url: False)
    monkeypatch.setattr(provider, "_ensure_managed", lambda: managed)
    assert provider._server() == managed.base_url
    provider._manage_server = False
    assert provider._server() == "http://opencode.invalid"
    monkeypatch.setattr(provider, "_is_reachable", lambda url: True)
    assert provider._server() == "http://opencode.invalid"


@pytest.mark.parametrize("outcome,expected", [(200, True), (401, False), (OSError(), False)])
def test_reachability(monkeypatch, outcome, expected):
    get = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=httpx.Response(outcome))
    monkeypatch.setattr(httpx, "get", get)
    assert provider_module.OpenCodeProvider._is_reachable("http://offline.invalid") is expected
    get.assert_called_once_with("http://offline.invalid/config", timeout=5.0)


async def test_health_uses_managed_endpoint_and_auth_fallback(http, monkeypatch):
    provider = provider_module.OpenCodeProvider()
    provider._managed = SimpleNamespace(base_url="http://managed.invalid", _auth_headers=lambda: {"Authorization": "Basic offline"})
    assert provider._auth_headers() == {"Authorization": "Basic offline"}
    monkeypatch.setattr(provider, "is_installed", lambda: True)
    http(lambda request: httpx.Response(200, json={}))
    health = await provider.health()
    assert health.available and health.mode == "local"
    assert health.detail == "OpenCode server running at http://managed.invalid"
    provider._managed = object()
    assert provider._auth_headers() == {}


@pytest.mark.parametrize("catalogue,model,expected", [
    ({"opencode": ["a", "a", "b"], "other": ["c"]}, "default", ["a", "b"]),
    ({"one": ["a"], "two": ["a", "b"]}, "default", ["a", "b"]),
    ({}, "chosen", ["chosen"]), ({}, "default", []),
])
async def test_catalogue_order_deduplication_and_model_fallback(http, monkeypatch, catalogue, model, expected):
    provider = provider_module.OpenCodeProvider(model=model)
    monkeypatch.setattr(provider, "_is_reachable", lambda url: True)
    http(lambda request: httpx.Response(200, json={"providers": [
        {"name": pid, "models": dict.fromkeys(models, {})} for pid, models in catalogue.items()
    ] + [{"models": {"ignored": {}}}]}))
    assert await provider.list_models() == expected
    assert provider._find_provider_for_model(catalogue, "missing") == "opencode"
    provider._model_provider_cache = {"old": "cached"}
    provider.model = "new"
    assert provider.model == "new" and provider._model_provider_cache is None


@pytest.mark.parametrize("outcome", [503, "bad-json", httpx.ConnectError("offline")])
async def test_catalogue_failure_returns_empty(http, monkeypatch, outcome):
    provider = provider_module.OpenCodeProvider()
    monkeypatch.setattr(provider, "_is_reachable", lambda url: True)

    def handler(request):
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(200, content=b"bad") if outcome == "bad-json" else httpx.Response(outcome)

    http(handler)
    assert await provider._catalogue() == {}


async def test_generate_native_session_payload_auth_trace_and_prompt(http, monkeypatch):
    provider = provider_module.OpenCodeProvider(model="chosen", username="user", password="synthetic")
    monkeypatch.setattr(provider, "_is_reachable", lambda url: True)
    monkeypatch.setattr(provider_module, "get_traceparent_header", lambda: "offline-trace")
    data = {"parts": [{"type": "text", "text": " first "}, {"type": "tool", "text": "ignored"},
                      None, {"type": "text", "text": 42}, {"type": "text", "text": ""},
                      {"type": "text", "text": "second"}]}

    def handler(request):
        assert request.headers["authorization"] == "Basic " + base64.b64encode(b"user:synthetic").decode()
        if request.url.path == "/session":
            assert json.loads(request.content) == {}
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path == "/config/providers":
            return httpx.Response(200, json={"providers": [{"id": "cloud", "models": {"chosen": {}}}]})
        assert request.url.path == "/session/session-1/message"
        assert request.headers["traceparent"] == "offline-trace"
        payload = json.loads(request.content)
        assert payload == {
            "providerId": "cloud", "model": {"id": "chosen", "providerID": "cloud", "modelID": "chosen"},
            "parts": [{"type": "text", "text": "<system>\ncontext\n</system>\n\n<assistant>\nprevious\n</assistant>\n\n<user>\nhello\n</user>"}],
        }
        return httpx.Response(200, json=data)

    calls = http(handler)
    request = RequestEnvelope(user_id="test", input="hello", correlation_id="ci",
                              context_summary="context", messages=[{"role": "assistant", "content": "previous"}])
    result = await provider.generate(request)
    assert (result.text, result.provider, result.model, result.raw) == ("first \nsecond", "opencode", "chosen", data)
    assert len(calls) == 3
    assert provider._extract_text([]) == ""


@pytest.mark.parametrize("outcome,error,message", [
    (httpx.ConnectError("offline"), ConnectionError, "Cannot connect"),
    (httpx.ReadTimeout("late"), TimeoutError, "timed out"),
    (httpx.RemoteProtocolError("bad"), RuntimeError, "unexpected response"),
    (503, RuntimeError, "HTTP 503"),
    ("bad-json", RuntimeError, "unexpected response"),
    ({}, RuntimeError, "no session id"),
    ({"id": "s", "empty": True}, RuntimeError, "empty response"),
])
async def test_generate_errors_are_actionable(http, monkeypatch, outcome, error, message):
    provider = provider_module.OpenCodeProvider()
    monkeypatch.setattr(provider, "_is_reachable", lambda url: True)

    def handler(request):
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome)
        if outcome == "bad-json":
            return httpx.Response(200, content=b"bad")
        return httpx.Response(200, json=outcome if request.url.path == "/session" else {})

    http(handler)
    with pytest.raises(error, match=message):
        await provider.generate(RequestEnvelope(user_id="test", input="hello", correlation_id="ci"))


def test_binary_search_fallback_and_missing_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(server_module.os.path, "expanduser", lambda path: str(tmp_path))
    assert server_module.find_binary() is None
    binary = tmp_path / ".opencode" / "bin" / "opencode.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("not executable", encoding="utf-8")
    assert server_module.find_binary() == str(binary)
    assert server_module._resolve_launcher(str(tmp_path / "missing.exe")) is None
    assert server_module._resolve_launcher(str(tmp_path / "missing.cmd")) is None
    assert server_module._resolve_launcher("unknown.sh") is None
    wrapper = tmp_path / "opencode.ps1"
    wrapper.write_text("no binary in wrapper", encoding="utf-8")
    assert server_module._resolve_launcher(str(wrapper)) is None


def test_launcher_sibling_fallback(monkeypatch, tmp_path):
    wrapper = tmp_path / "bin" / "opencode.cmd"
    wrapper.parent.mkdir()
    wrapper.write_text('"missing/node_modules/opencode-ai/bin/opencode.exe"', encoding="utf-8")
    binary = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("fake", encoding="utf-8")
    assert server_module._resolve_launcher(str(wrapper)) == str(binary.resolve())


def test_free_port_uses_loopback_ephemeral_socket(monkeypatch):
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.getsockname.return_value = ("127.0.0.1", 12345)
    monkeypatch.setattr(server_module.socket, "socket", Mock(return_value=sock))
    assert server_module._free_port() == 12345
    sock.bind.assert_called_once_with(("127.0.0.1", 0))
    sock.__exit__.assert_called_once()


@pytest.mark.parametrize("binary", [None, "opencode.cmd"])
def test_start_rejects_missing_or_wrapper_binary(monkeypatch, binary):
    monkeypatch.setattr(server_module, "find_binary", lambda: binary)
    assert server_module.ManagedOpenCodeServer().start() is False
    subprocess.Popen.assert_not_called()


def test_start_replaces_running_process_and_builds_isolated_command(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "find_binary", lambda: "C:/fake/opencode.exe")
    monkeypatch.setattr(server_module, "_free_port", lambda: 12345)
    monkeypatch.setattr(server_module.os.path, "expanduser", lambda path: str(tmp_path))
    monkeypatch.setattr(server_module.secrets, "token_urlsafe", lambda size: "synthetic-password")
    proc = Mock(pid=123)
    proc.poll.return_value = None
    popen = Mock(return_value=proc)
    monkeypatch.setattr(subprocess, "Popen", popen)
    server = server_module.ManagedOpenCodeServer(username="")
    server._process = Mock(pid=99)
    server._process.poll.return_value = None

    def stop():
        server._process = None

    stopped = Mock(side_effect=stop)
    monkeypatch.setattr(server, "stop", stopped)
    job = Mock(return_value="fake-job")
    monkeypatch.setattr(server, "_create_job", job)
    assert server.start() is True
    stopped.assert_called_once_with()
    args, kwargs = popen.call_args
    assert args[0] == ["C:/fake/opencode.exe", "serve", "--port", "12345", "--hostname", "127.0.0.1"]
    assert kwargs["env"]["OPENCODE_SERVER_USERNAME"] == "opencode"
    assert kwargs["env"]["OPENCODE_SERVER_PASSWORD"] == "synthetic-password"
    assert server.running and server.port == 12345 and server._started
    assert server.base_url == "http://127.0.0.1:12345" and server._job == "fake-job"
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    job.assert_called_once_with(proc)


@pytest.mark.parametrize("handle", [0, 12])
def test_windows_job_configuration_is_simulated(monkeypatch, handle):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 88
    kernel.OpenProcess.return_value = handle
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    assert server_module.ManagedOpenCodeServer()._create_job(Mock(pid=123)) == 88
    args = kernel.SetInformationJobObject.call_args.args
    assert args[:2] == (88, 9)
    assert args[2]._obj.LimitFlags == 0x2000
    if handle:
        kernel.AssignProcessToJobObject.assert_called_once_with(88, handle)
    else:
        kernel.AssignProcessToJobObject.assert_not_called()


@pytest.mark.parametrize("running,ready,start,expected", [
    (True, True, True, True), (True, False, True, False),
    (False, False, False, False), (False, True, True, True),
])
def test_ensure_running_reuses_or_restarts(monkeypatch, running, ready, start, expected):
    server = server_module.ManagedOpenCodeServer()
    if running:
        server._process = Mock()
        server._process.poll.return_value = None
    reap = Mock(return_value=[])
    monkeypatch.setattr(server_module, "reap_orphaned_managed", reap)
    monkeypatch.setattr(server, "_ready", Mock(return_value=ready))
    monkeypatch.setattr(server, "start", Mock(return_value=start))
    monkeypatch.setattr(server, "stop", Mock())
    assert server.ensure_running() is expected
    reap.assert_called_once_with()
    assert server.stop.call_count == int(running and not ready)
    assert server.start.call_count == int(not (running and ready))


@pytest.mark.parametrize("responses,clock,running,expected", [
    ([httpx.ConnectError("offline"), httpx.Response(503), httpx.Response(200)], [0, 0, 1, 2], True, True),
    ([], [0, 46], True, False), ([], [0, 0], False, False),
])
def test_readiness_retries_and_has_deadline(monkeypatch, responses, clock, running, expected):
    server = server_module.ManagedOpenCodeServer(base_url="http://offline.invalid", password="test")
    if running:
        server._process = Mock()
        server._process.poll.return_value = None
    get = Mock(side_effect=responses)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(server_module.time, "time", Mock(side_effect=clock))
    sleep = Mock()
    monkeypatch.setattr(server_module.time, "sleep", sleep)
    assert server._ready() is expected
    assert get.call_count == len(responses)
    assert sleep.call_count == max(0, len(responses) - 1)


@pytest.mark.parametrize("outcome,detail", [(200, "answering /config"), (503, "HTTP 503"), (OSError("offline"), "offline")])
def test_managed_health_details(monkeypatch, outcome, detail):
    server = server_module.ManagedOpenCodeServer(base_url="http://offline.invalid", username="u", password="p")
    assert server.health()["running"] is False
    server._process = Mock()
    server._process.poll.return_value = None
    get = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=httpx.Response(outcome))
    monkeypatch.setattr(httpx, "get", get)
    health = server.health()
    assert health["running"] is True and detail in health["detail"]
    assert get.call_args.kwargs["headers"] == {"Authorization": "Basic dTpw"}


@pytest.mark.parametrize("waits", [
    [subprocess.TimeoutExpired("fake", 5), None],
    [subprocess.TimeoutExpired("fake", 5), subprocess.TimeoutExpired("fake", 5)],
    [OSError("wait failed")],
])
def test_stop_fallback_clears_state_even_when_wait_fails(monkeypatch, waits):
    server = server_module.ManagedOpenCodeServer()
    proc = Mock(pid=123)
    proc.poll.return_value = None
    proc.wait.side_effect = waits
    server._process, server._job, server._started = proc, "job", True
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    server.stop()
    assert server._process is None and server._job is None and not server._started
    assert not server.running
    proc.terminate.assert_called_once_with()
    assert proc.kill.call_count == int(isinstance(waits[0], subprocess.TimeoutExpired))
    assert run.call_args_list[0].args[0] == ["taskkill", "/PID", "123", "/T"]
    if len(waits) == 2:
        assert run.call_args_list[1].args[0] == ["taskkill", "/PID", "123", "/T", "/F"]


def test_tree_kill_errors_are_best_effort(monkeypatch):
    server = server_module.ManagedOpenCodeServer()
    server._process = Mock()
    server._process.poll.return_value = None
    server._process.terminate.side_effect = OSError("terminate")
    server._process.kill.side_effect = OSError("kill")
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=OSError("taskkill")))
    server._terminate_tree(123)
    server._force_kill_tree(123)
    server._process.terminate.assert_called_once_with()
    server._process.kill.assert_called_once_with()


@pytest.mark.parametrize("singleton,kill_fails", [(False, False), (True, False), (True, True)])
def test_reaper_only_targets_owned_orphans(monkeypatch, singleton, kill_fails):
    row = {"ProcessId": 20, "ParentProcessId": 999, "CommandLine": "C:/fake/opencode.exe serve --hostname 127.0.0.1"}
    rows = row if singleton else [row,
        {**row, "ProcessId": 21, "ParentProcessId": 1},
        {**row, "ProcessId": 22, "CommandLine": "other.exe serve --hostname 127.0.0.1"},
        {**row, "ProcessId": 23, "CommandLine": "opencode.exe run"},
        {"ProcessId": "invalid"}, {**row, "ProcessId": 24}]
    monkeypatch.setattr(server_module, "find_binary", lambda: "C:/fake/opencode.exe")
    monkeypatch.setattr(subprocess, "check_output", Mock(side_effect=[json.dumps(rows).encode(), b"1 20 21 22 23 not-a-pid"]))
    run = Mock(side_effect=OSError("denied") if kill_fails else None)
    monkeypatch.setattr(subprocess, "run", run)
    assert server_module.reap_orphaned_managed() == ([] if kill_fails else [20])
    run.assert_called_once_with(["taskkill", "/PID", "20", "/T", "/F"], capture_output=True, timeout=10)


def test_reaper_tolerates_unavailable_process_inventory(monkeypatch):
    monkeypatch.setattr(server_module, "find_binary", lambda: None)
    monkeypatch.setattr(subprocess, "check_output", Mock(side_effect=OSError("no powershell")))
    assert server_module.reap_orphaned_managed() == []


def test_managed_singleton_credentials_and_shutdown(monkeypatch):
    monkeypatch.setenv("CHIKY_OPENCODE_USERNAME", "synthetic-user")
    monkeypatch.setenv("CHIKY_OPENCODE_PASSWORD", "synthetic-password")
    server = server_module.get_managed_server()
    assert server_module.get_managed_server() is server
    assert (server.username, server.password) == ("synthetic-user", "synthetic-password")
    monkeypatch.setattr(server_module, "find_binary", lambda: "C:/fake/opencode.exe")
    assert server.binary() == "C:/fake/opencode.exe"
    stop = Mock()
    monkeypatch.setattr(server, "stop", stop)
    server_module.stop_managed_server()
    server_module.stop_managed_server()
    stop.assert_called_once_with()
    assert server_module._managed_server is None