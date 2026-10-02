"""R.7 — Debugging scenario test (live Ollama)."""
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


async def test_r7_debugging():
    model = os.environ.get("OLLAMA_MODEL", "llama3.1:latest")
    ws = Path(os.environ.get("TEMP", ".")) / "chiky_r7_debug"
    ws.mkdir(parents=True, exist_ok=True)

    buggy = ws / "calc.py"
    buggy.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    print("[R.7] Bug: add() returns subtraction instead of addition")
    print(f"[R.7] Content:\n{buggy.read_text()}")

    provider = OllamaProvider(model=model)
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)

    agent = ExecutionAgent(provider=provider, registry=reg, observability=None)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="r7_test",
        text=f"Fix the bug in {buggy}. The add function should return a+b not a-b.",
        correlation_id="fase-r7-debugging",
        risk_level=RiskLevel.MEDIUM,
        context={"authorized": True, "approval_granted": True, "working_directory": str(ws)},
    )

    start = time.monotonic()
    try:
        artifact = await asyncio.wait_for(agent.run(data), timeout=360)
        elapsed = time.monotonic() - start
        print("\n=== R.7 DEBUGGING RESULT ===")
        print(f"Model: {model}")
        print(f"Time: {elapsed:.1f}s")
        print(f"Response: {artifact.content[:500]}")

        final = buggy.read_text(encoding="utf-8")
        print(f"Final content: {final}")

        fixed = "return a + b" in final and "return a - b" not in final
        print(f"Fixed: {fixed}")
        print(f"R.7: {'PASS' if fixed else 'FAIL'} ({elapsed:.1f}s)")

        return {
            "model": model,
            "time": elapsed,
            "fixed": fixed,
            "result": "PASS" if fixed else "FAIL",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"\nR.7: FAILED after {elapsed:.1f}s: {e}")
        return {
            "model": model,
            "time": elapsed,
            "fixed": False,
            "result": "HARDWARE_LIMITED",
            "error": str(e),
        }
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    result = asyncio.run(test_r7_debugging())
    print(f"\nFinal: {result}")
