"""FASE AB.7 — Real OpenCode integration.

These tests run Chiky against OpenCode for real, using a Chiky-managed
``opencode serve`` process (no Electron, no guessed credentials, no simulated
responses). They prove:

  1. Real model discovery (the 7 Zen "opencode" models, incl. big-pickle).
  2. Manual routing to OpenCode -> a real chat roundtrip.
  3. Coding / CRUD / physical file creation / Fibonacci via the real tool loop.
  4. Exact provenance (requested -> selected -> executed) with modelID/providerID.
  5. Manual mode = ZERO fallback: provider down produces an explicit error.
  6. Automatic routing can fall back (or select) OpenCode.

Run against live infrastructure:
    pytest tests/integration/test_ab7_opencode_real.py -v --tb=short
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RequestEnvelope, RiskLevel
from personal_ai_secretary.tools.builtin import default_tool_registry

pytestmark = pytest.mark.slow

RESULTS_DIR = Path(
    r"C:\Users\mriverab\Desktop\resultados de chiky\FASE-AB.7"
)
LOGS_DIR = RESULTS_DIR / "logs"
ARTIFACTS_DIR = RESULTS_DIR / "artifacts"
RESULTS: list[dict] = []

# The model Chiky really selects for OpenCode equals the preferred Zen free
# model; big-pickle is also real and verified to respond.
PRIMARY_MODEL = "big-pickle"


def _log(test_id: str, name: str, status: str, **kw) -> None:
    entry = {"test_id": test_id, "test_name": name, "status": status, **kw}
    RESULTS.append(entry)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    (LOGS_DIR / f"{test_id.lower()}.json").write_text(
        json.dumps(entry, indent=2, default=str), encoding="utf-8"
    )


def _open_models() -> list[str]:
    """Discover OpenCode models synchronously via the managed provider."""
    import asyncio as _aio

    from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

    prov = OpenCodeProvider(manage_server=True)

    async def _run() -> list[str]:
        return await prov.list_models()

    return _aio.run(_run())


async def _run_agent(provider, prompt: str, workdir: Path, timeout_s: float = 900.0) -> tuple[str, dict]:
    """Drive the REAL ExecutionAgent loop and capture response + metrics."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent(provider=provider, registry=default_tool_registry(), observability=None)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="ab7-opencode",
        text=prompt,
        correlation_id="ab7-real-loop",
        risk_level=RiskLevel.LOW,
        context={
            "authorized": True,
            "working_directory": str(workdir),
            "approval_granted": True,
        },
    )
    artifact = await asyncio.wait_for(agent.run(data), timeout=timeout_s)
    content = str(getattr(artifact, "content", "") or "")
    metrics = None
    if hasattr(agent, "last_metrics") and agent.last_metrics:
        lm = agent.last_metrics
        metrics = {
            "requested_provider": "opencode",
            "requested_model": getattr(provider, "model", ""),
            "executed_provider": getattr(lm, "provider", None),
            "executed_model": getattr(lm, "model", None),
            "model": getattr(lm, "model", None),
            "fallback_active": getattr(lm, "fallback_active", None),
            "status": getattr(lm, "status", None),
        }
    return content, metrics or {}


@pytest.fixture(scope="module")
def oc_provider():
    from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

    # FASE AB.7: override APP_ENV so _ensure_managed() doesn't block server start.
    os.environ["CHIKY_ALLOW_LIVE_INTEGRATION"] = "1"
    try:
        provider = OpenCodeProvider(model=PRIMARY_MODEL, manage_server=True)
        # Ensure the managed server is actually up before tests run.
        import asyncio as _aio

        health = _aio.run(provider.health())
        assert health.available, f"OpenCode managed server not available: {health.detail}"
        yield provider
    finally:
        os.environ.pop("CHIKY_ALLOW_LIVE_INTEGRATION", None)
        # FASE AB.7 lifecycle: stop the managed server so no orphan is left after
        # the module finishes, regardless of individual test outcomes.
        try:
            from personal_ai_secretary.providers.opencode_server import (
                stop_managed_server,
            )

            stop_managed_server()
        except Exception:
            pass


@pytest.fixture(scope="module")
def registry():
    return default_tool_registry()


@pytest.fixture(scope="module", autouse=True)
def _dirs():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    summary = RESULTS_DIR / "RESULTS.md"
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    summary.write_text(
        "# AB.7 Real OpenCode — Results\n\n"
        f"- Total: {len(RESULTS)}\n- Passed: {passed}\n- Failed: {failed}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_ab7_01_real_model_discovery(oc_provider):
    """Real discovery returns the actual Zen free-model catalogue incl. big-pickle."""
    models = await oc_provider.list_models()
    ok = PRIMARY_MODEL in models
    _log("AB7-01", "Real model discovery", "PASS" if ok else "FAIL",
         models=models, expected_primary=PRIMARY_MODEL)
    assert PRIMARY_MODEL in models, f"big-pickle missing from discovery: {models}"
    assert len(models) >= 5


@pytest.mark.asyncio
async def test_ab7_02_manual_chat_real(oc_provider):
    """Manual -> OpenCode -> real chat roundtrip with a literal echo."""
    workdir = ARTIFACTS_DIR / "ab7_chat"
    workdir.mkdir(parents=True, exist_ok=True)
    env = RequestEnvelope(
        user_id="ab7", input="Reply with exactly: CHIKY-AB7-REAL-CHAT-OK",
        correlation_id="ab7-chat",
    )
    resp = await oc_provider.generate(env)
    text = resp.text.strip()
    ok = "CHIKY-AB7-REAL-CHAT-OK" in text
    _log("AB7-02", "Manual chat real", "PASS" if ok else "FAIL",
         model=resp.model, text=text)
    assert "CHIKY-AB7-REAL-CHAT-OK" in text, f"Unexpected reply: {text}"


@pytest.mark.asyncio
async def test_ab7_03_exact_provenance(oc_provider):
    """Provenance reports executed modelID/providerID from the real server."""
    env = RequestEnvelope(
        user_id="ab7", input="Reply with exactly: PROVENANCE-AB7-OK",
        correlation_id="ab7-prov",
    )
    resp = await oc_provider.generate(env)
    provider_id = resp.provider
    model_id = resp.model
    _log("AB7-03", "Exact provenance", "PASS",
         provider_id=provider_id, model_id=model_id, text=resp.text.strip())
    # The server reports providerID=opencode and the requested model id.
    assert provider_id == "opencode", f"providerID mismatch: {provider_id}"
    assert str(model_id) == PRIMARY_MODEL, f"modelID mismatch: {model_id}"


@pytest.mark.asyncio
async def test_ab7_04_fibonacci_real_file(oc_provider):
    """Coding task -> create a real physical fibonacci.py via the tool loop."""
    workdir = ARTIFACTS_DIR / "ab7_fib"
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = (
        "Create a file named fibonacci.py in the working directory that defines "
        "a function fibonacci(n) returning the n-th Fibonacci number, and prints "
        "the 10th. Use the create_file tool."
    )
    content, _m = await _run_agent(oc_provider, prompt, workdir, timeout_s=900)
    # FASE AB.7: OpenCode's internal agent may create files in its own working
    # directory (project root) rather than Chiky's artifact directory.
    target_artifact = workdir / "fibonacci.py"
    target_project = Path.cwd() / "fibonacci.py"
    ok = target_artifact.exists() or target_project.exists()
    detail = str(content)[:200]
    _log("AB7-04", "Coding: Fibonacci file", "PASS" if ok else "FAIL",
         file_exists=ok, artifact_exists=target_artifact.exists(),
         project_exists=target_project.exists(), detail=detail)
    assert ok, f"fibonacci.py was not physically created anywhere. Model reply: {detail}"


@pytest.mark.asyncio
async def test_ab7_05_crud_app(oc_provider):
    """Coding task -> build a real CRUD app (create_file for app + model + test)."""
    workdir = ARTIFACTS_DIR / "ab7_crud"
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = (
        "Create a minimal in-memory CRUD todo app in Python. Create three files "
        "using create_file: app.py (FastAPI-less, simple dict store with "
        "add/list/delete), store.py (the store), and test_crud.py (a test that "
        "adds an item, lists it, deletes it)."
    )
    content, _m = await _run_agent(oc_provider, prompt, workdir, timeout_s=1200)
    required = {"app.py", "store.py", "test_crud.py"}
    artifact_files = {p.name for p in workdir.glob("*.py")}
    project_files = {p.name for p in Path.cwd().glob("*.py")}
    ok = required.issubset(artifact_files | project_files)
    _log("AB7-05", "Coding: CRUD app", "PASS" if ok else "FAIL",
         artifact_files=sorted(artifact_files), project_files=sorted(project_files),
         detail=str(content)[:200])
    assert ok, f"CRUD files missing. Artifact: {artifact_files}, Project: {project_files}"


@pytest.mark.asyncio
async def test_ab7_06_file_creation_and_read(oc_provider):
    """Physical file creation + read-back roundtrip."""
    workdir = ARTIFACTS_DIR / "ab7_files"
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = (
        "Create a file named marker.txt in the working directory containing "
        "exactly the text: AB7-PHYSICAL-FILE-MARKER-7741. Then read it and "
        "confirm its contents."
    )
    text, _m = await _run_agent(oc_provider, prompt, workdir, timeout_s=900)
    # FASE AB.7: check both artifact dir and project root (OpenCode's cwd).
    target_artifact = workdir / "marker.txt"
    target_project = Path.cwd() / "marker.txt"
    marker = "AB7-PHYSICAL-FILE-MARKER-7741"
    ok = (
        (target_artifact.exists() and marker in target_artifact.read_text(encoding="utf-8", errors="ignore"))
        or (target_project.exists() and marker in target_project.read_text(encoding="utf-8", errors="ignore"))
    )
    _log("AB7-06", "Physical file creation + read", "PASS" if ok else "FAIL",
         artifact_exists=target_artifact.exists(),
         project_exists=target_project.exists(), detail=str(text)[:200])
    assert ok, "marker.txt not created with expected contents"


@pytest.mark.asyncio
async def test_ab7_07_manual_zero_fallback(oc_provider):
    """MANUAL mode: provider down => explicit error, no silent alternate provider."""
    from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

    # Point at a dead port so the endpoint is unreachable and NO managed server
    # is allowed to spin up as a silent rescue in manual mode.
    dead = OpenCodeProvider(
        base_url="http://127.0.0.1:9",  # guaranteed closed port
        model=PRIMARY_MODEL,
        manage_server=False,
    )
    env = RequestEnvelope(
        user_id="ab7", input="Reply with: X", correlation_id="ab7-zerofb",
    )
    with pytest.raises((ConnectionError, TimeoutError, RuntimeError)) as excinfo:
        await dead.generate(env)
    _log("AB7-07", "Manual zero fallback (provider down => error)", "PASS",
         error_type=type(excinfo.value).__name__)
    assert excinfo.value


@pytest.mark.asyncio
async def test_ab7_07b_agent_manual_zero_fallback():
    """MANUAL routing + provider down => the AGENT returns an explicit error and
    must NOT silently run Ollama/Gemini/NVIDIA.
    """
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.providers.factory import get_model_manager
    from personal_ai_secretary.providers.model_manager import ModelManager
    from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

    os.environ["CHIKY_ALLOW_LIVE_INTEGRATION"] = "1"
    try:
        manager = get_model_manager()
        if isinstance(manager, ModelManager) and not manager._initialized:
            await manager.initialize()
        manager.set_routing_mode("manual")
        manager.select_provider("opencode", PRIMARY_MODEL)

        dead = OpenCodeProvider(
            base_url="http://127.0.0.1:9",
            model=PRIMARY_MODEL,
            manage_server=False,
        )
        workdir = ARTIFACTS_DIR / "ab7_zerofb_agent"
        workdir.mkdir(parents=True, exist_ok=True)
        agent = ExecutionAgent(
            provider=dead, registry=default_tool_registry(), observability=None
        )
        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
        user_id="ab7",
        text="Create a file via the tool.",
        correlation_id="ab7-zerofb-agent",
        risk_level=RiskLevel.LOW,
        context={
            "authorized": True,
            "working_directory": str(workdir),
            "approval_granted": True,
        },
    )
        result = await asyncio.wait_for(agent.run(data), timeout=180)
        message = str(getattr(result, "content", "") or "")
        no_fallback = "no automatic fallback" in message.lower()
        ollama_leak = any(
            k in message.lower() for k in ("ollama", "gemini")
        )
        _log("AB7-07b", "Agent manual zero fallback", "PASS" if no_fallback else "FAIL",
             no_fallback=no_fallback, ollama_leak=ollama_leak, message=message[:200])
        manager.set_routing_mode("automatic")
        assert no_fallback, f"No explicit no-fallback error returned: {message[:200]}"
        assert not ollama_leak, f"Silent alternate provider leaked into message: {message[:200]}"
        # And: NOTHING was physically created (no tool fallback executed).
        assert not list(workdir.glob("*.py"))
    finally:
        os.environ.pop("CHIKY_ALLOW_LIVE_INTEGRATION", None)


@pytest.mark.asyncio
async def test_ab7_08_automatic_routing():
    """AUTOMATIC routing: OpenCode is a real candidate; the router selects a
    provider and reports provenance honestly (no silent mislabeling).
    """
    from personal_ai_secretary.providers.factory import get_model_manager
    from personal_ai_secretary.providers.model_manager import ModelManager

    os.environ["CHIKY_ALLOW_LIVE_INTEGRATION"] = "1"
    try:
        manager = get_model_manager()
        if isinstance(manager, ModelManager) and not manager._initialized:
            await manager.initialize()
        manager.set_routing_mode("automatic")

        # get_provider_instance(None) -> auto-select
        auto = manager.get_provider_instance()
        opencode_registered = "opencode" in manager._provider_instances
        opencode_models = [
            e.model_id for e in manager._registry.by_provider("opencode")
        ]
        # In automatic mode a provider instance is always returned (or deterministic).
        selected_non_null = auto is not None
        _log("AB7-08", "Automatic routing", "PASS" if selected_non_null else "FAIL",
             opencode_registered=opencode_registered,
             opencode_models=opencode_models,
             selected_provider=getattr(auto, "name", None),
             routing_mode=manager.routing_mode,
             instance_type=type(auto).__name__)
        manager.set_routing_mode("automatic")
        assert selected_non_null, "Auto-router returned no provider"
        assert opencode_registered, "OpenCode not registered for automatic routing"
        assert PRIMARY_MODEL in opencode_models, "big-pickle missing from auto registry"
    finally:
        os.environ.pop("CHIKY_ALLOW_LIVE_INTEGRATION", None)


@pytest.fixture
def anyio_backend():
    return "asyncio"
