"""FASE Q — live acceptance script (Q.11 + Q.13 data collection).

Runs a few scenarios against real Ollama models and prints per-run
metrics: elapsed, rounds, tool calls, final task state, latency class.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput, RiskLevel
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.datetime_tool import register_datetime_tools
from personal_ai_secretary.tools.development import register_development_tools
from personal_ai_secretary.tools.filesystem import register_filesystem_tools
from personal_ai_secretary.tools.registry import ToolRegistry

WORKSPACE = Path.home() / "AppData" / "Local" / "Temp" / "opencode" / "q_live_ws"


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)
    return reg


def make_input(text: str) -> AgentInput:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="q_acceptance",
        text=text,
        correlation_id="fase-q-live",
        risk_level=RiskLevel.LOW,
        context={
            "authorized": True,
            "working_directory": str(WORKSPACE),
            "approval_granted": True,
        },
    )


async def run_scenario(model: str, label: str, text: str) -> dict:
    agent = ExecutionAgent(
        provider=OllamaProvider(model=model), registry=build_registry()
    )
    start = time.monotonic()
    artifact = await agent.run(make_input(text))
    elapsed = time.monotonic() - start
    m = agent.last_metrics
    row = {
        "scenario": label,
        "model": model,
        "elapsed_s": round(elapsed, 1),
        "rounds": m.rounds,
        "tool_calls": m.tool_calls,
        "final_task_state": m.final_task_state,
        "latency_class": m.latency_classification,
        "directives": m.backend_directives,
        "unproductive_stops": m.unproductive_loop_detections,
        "outcome": m.final_outcome,
    }
    print(json.dumps(row), flush=True)
    print(f"--- content ({len(artifact.content)} chars) ---")
    print(artifact.content[:600].encode("ascii", errors="replace").decode("ascii"))
    print("==============================", flush=True)
    return row


async def main() -> None:
    target = WORKSPACE / "q_note.txt"
    if target.exists():
        target.unlink()

    results = []
    # L1 — simple create (llama3.1): P baseline PASS; must stay PASS.
    results.append(await run_scenario(
        "llama3.1:latest", "L1_create",
        f"Create a file at the absolute path {target} with the "
        "content 'hello from fase q'.",
    ))
    print(f"L1 file exists: {target.exists()}", flush=True)

    # L2 — debug loop (llama3.1): P baseline D2 model-limited read-loop.
    buggy = WORKSPACE / "q_buggy.py"
    buggy.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    results.append(await run_scenario(
        "llama3.1:latest", "L2_debug",
        f"Fix the bug in the file {buggy}: add() should return "
        "the sum of a and b.",
    ))
    fixed = "return a + b" in buggy.read_text(encoding="utf-8")
    print(f"L2 file actually fixed: {fixed}", flush=True)

    # L3 — cross-model sanity (deepseek-coder-v2).
    ds_target = WORKSPACE / "q_ds.txt"
    if ds_target.exists():
        ds_target.unlink()
    results.append(await run_scenario(
        "deepseek-coder-v2:latest", "L3_deepseek_create",
        f"Create a file at the absolute path {ds_target} containing "
        "'deepseek was here'.",
    ))
    print(f"L3 file exists: {ds_target.exists()}", flush=True)

    out_path = WORKSPACE.parent / "q_live_results.json"
    payload = json.dumps(results, indent=2)
    await asyncio.to_thread(out_path.write_text, payload, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
