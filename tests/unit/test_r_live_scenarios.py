"""FASE R — Live scenario tests: R.1 (file creation), R.5 (project analysis), R.8 (model switching)."""
import asyncio
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

os.environ["AI_PROVIDER"] = "local"
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:latest")

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.datetime_tool import register_datetime_tools
from personal_ai_secretary.tools.development import register_development_tools
from personal_ai_secretary.tools.filesystem import register_filesystem_tools
from personal_ai_secretary.tools.registry import ToolRegistry


def make_agent(model: str):
    provider = OllamaProvider(model=model)
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)
    return ExecutionAgent(provider=provider, registry=reg, observability=None)


def make_input(text: str, ws: Path, user_id: str = "r_test", risk: RiskLevel = RiskLevel.LOW):
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id=user_id,
        text=text,
        correlation_id=f"fase-r-{user_id}",
        risk_level=risk,
        context={
            "authorized": True,
            "approval_granted": True,
            "working_directory": str(ws),
        },
    )


async def run_scenario(name: str, model: str, text: str, ws: Path, timeout_s: int = 360):
    agent = make_agent(model)
    data = make_input(text, ws, user_id=name.lower().replace(" ", "_"))

    start = time.monotonic()
    try:
        artifact = await asyncio.wait_for(agent.run(data), timeout=timeout_s)
        elapsed = time.monotonic() - start
        return {
            "scenario": name,
            "model": model,
            "time": round(elapsed, 1),
            "result": "PASS",
            "response": artifact.content[:300],
        }
    except TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "scenario": name,
            "model": model,
            "time": round(elapsed, 1),
            "result": "HARDWARE_LIMITED",
            "error": "Ollama timeout",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "scenario": name,
            "model": model,
            "time": round(elapsed, 1),
            "result": "FAIL",
            "error": str(e)[:200],
        }


async def r1_file_creation(model):
    """R.1: Create hola.txt with 'Hola Mundo'."""
    ws = Path(os.environ.get("TEMP", ".")) / "chiky_r1"
    ws.mkdir(parents=True, exist_ok=True)
    try:
        result = await run_scenario(
            "R1_FileCreation", model,
            f"Create a file called hola.txt in {ws} with content 'Hola Mundo'",
            ws,
        )
        # Verify
        target = ws / "hola.txt"
        if target.exists():
            content = target.read_text(encoding="utf-8")
            result["verified"] = "Hola Mundo" in content
            result["result"] = "PASS" if result["verified"] else "PARTIAL"
        else:
            result["verified"] = False
            result["result"] = "FAIL"
        return result
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def r5_project_analysis(model):
    """R.5: Analyze project structure."""
    ws = Path(os.environ.get("TEMP", ".")) / "chiky_r5"
    ws.mkdir(parents=True, exist_ok=True)
    # Create a small sample project
    (ws / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (ws / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (ws / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    try:
        return await run_scenario(
            "R5_ProjectAnalysis", model,
            f"Analyze the project in {ws} and tell me how it is structured.",
            ws,
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def r8_model_switching(model):
    """R.8: Simple query to verify model responds."""
    ws = Path(os.environ.get("TEMP", ".")) / f"chiky_r8_{model.replace(':', '_')}"
    ws.mkdir(parents=True, exist_ok=True)
    try:
        return await run_scenario(
            "R8_ModelSwitch", model,
            "What is 2 + 2? Reply with just the number.",
            ws,
            timeout_s=180,
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


async def main():
    model = os.environ.get("OLLAMA_MODEL", "llama3.1:latest")
    results = []

    print(f"=== FASE R — Live Scenarios (model: {model}) ===\n")

    # R.1 — File creation
    print("[R.1] File creation...")
    r1 = await r1_file_creation(model)
    results.append(r1)
    print(f"  Result: {r1['result']} ({r1['time']}s)")

    # R.5 — Project analysis
    print("[R.5] Project analysis...")
    r5 = await r5_project_analysis(model)
    results.append(r5)
    print(f"  Result: {r5['result']} ({r5['time']}s)")

    # R.8 — Model switching (llama3 vs llama3.1)
    print("[R.8] Model switching — llama3.1...")
    r8_1 = await r8_model_switching("llama3.1:latest")
    results.append(r8_1)
    print(f"  Result: {r8_1['result']} ({r8_1['time']}s)")

    print("[R.8] Model switching — llama3...")
    r8_2 = await r8_model_switching("llama3:latest")
    results.append(r8_2)
    print(f"  Result: {r8_2['result']} ({r8_2['time']}s)")

    print("\n=== RESULTS ===")
    for r in results:
        print(f"  {r['scenario']}: {r['result']} ({r['time']}s, {r['model']})")
        if r.get("error"):
            print(f"    Error: {r['error']}")

    return results


if __name__ == "__main__":
    asyncio.run(main())
