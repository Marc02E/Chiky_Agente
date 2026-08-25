"""FASE S.1.1 — Baseline measurement for CPU performance optimization.

Measures total_duration, llm_calls, tool_calls, command_calls, tool_rounds,
fix_cycles, context_chars, estimated_tokens, tool_result_chars,
system_prompt_chars, history_chars for each scenario.

No optimizations applied — pure baseline.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

os.environ["AI_PROVIDER"] = "local"

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.tools.registry import ToolRegistry
from personal_ai_secretary.tools.filesystem import register_filesystem_tools
from personal_ai_secretary.tools.development import register_development_tools
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.datetime_tool import register_datetime_tools
from personal_ai_secretary.providers.ollama import OllamaProvider


def build_agent(model: str) -> tuple[ExecutionAgent, ToolRegistry]:
    provider = OllamaProvider(model=model)
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)
    agent = ExecutionAgent(provider=provider, registry=reg, observability=None)
    return agent, reg


def extract_metrics(agent: ExecutionAgent, reg: ToolRegistry, elapsed: float) -> dict:
    m = agent.last_metrics
    if m is None:
        return {"error": "no metrics"}
    
    tool_result_chars = getattr(m, "tool_results_chars", 0)
    system_prompt_chars = getattr(m, "system_prompt_chars", 0)
    history_chars = getattr(m, "history_chars", 0)
    estimated_tokens = getattr(m, "estimated_context_tokens", 0)
    fix_cycles = getattr(m, "diagnosis_attempts", 0)
    command_count = getattr(m, "command_count", 0)
    
    return {
        "total_duration": round(elapsed, 1),
        "llm_calls": m.llm_calls,
        "tool_calls": m.tool_calls,
        "command_calls": command_count,
        "tool_rounds": m.rounds,
        "fix_cycles": fix_cycles,
        "context_chars": m.context_chars_approx,
        "estimated_tokens": estimated_tokens,
        "tool_result_chars": tool_result_chars,
        "system_prompt_chars": system_prompt_chars,
        "history_chars": history_chars,
        "model": m.model,
        "final_status": m.final_outcome,
        "dedup": m.deduplicated_calls,
        "llm_times": [round(t, 1) for t in m.llm_times],
        "tool_times": [round(t, 1) for t in m.tool_times],
        "llm_total": round(sum(m.llm_times), 1),
        "tool_total": round(sum(m.tool_times), 1),
        "backend_directives": m.backend_directives,
        "unproductive_detections": m.unproductive_loop_detections,
        "verification_status": m.verification_status,
        "state_transitions": m.state_transitions,
        "normalizer_metrics": reg.get_metrics(),
    }


async def run_scenario(name: str, model: str, request_text: str, context: dict, timeout: int = 300) -> dict:
    ws = Path(os.environ.get("TEMP", ".")) / f"chiky_s1_{name}"
    ws.mkdir(parents=True, exist_ok=True)
    
    agent, reg = build_agent(model)
    
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="fase_s1_baseline",
        text=request_text,
        correlation_id=f"fase-s1-{name}",
        risk_level=RiskLevel.LOW,
        context={**context, "working_directory": str(ws)},
    )
    
    start = time.monotonic()
    try:
        artifact = await asyncio.wait_for(agent.run(data), timeout=timeout)
        elapsed = time.monotonic() - start
        metrics = extract_metrics(agent, reg, elapsed)
        metrics["final_response"] = artifact.content[:500] if artifact.content else ""
        metrics["scenario"] = name
        metrics["model"] = model
        return metrics
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "scenario": name,
            "model": model,
            "total_duration": round(elapsed, 1),
            "final_status": "TIMEOUT",
            "llm_calls": 0,
            "tool_calls": 0,
            "error": f"Timeout after {timeout}s",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "scenario": name,
            "model": model,
            "total_duration": round(elapsed, 1),
            "final_status": "ERROR",
            "llm_calls": 0,
            "tool_calls": 0,
            "error": str(e),
        }
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)


async def baseline():
    results = []
    
    # Scenario 1: File creation (simple)
    r = await run_scenario(
        "file_create",
        "llama3.1:latest",
        "Create a file called hello.txt with content 'Hello World'",
        {"authorized": True, "approval_granted": True},
        timeout=180,
    )
    results.append(r)
    print(f"[baseline] file_create: {r['total_duration']}s, llm={r.get('llm_calls')}, tools={r.get('tool_calls')}, rounds={r.get('tool_rounds')}")
    
    # Scenario 2: Project analysis
    r = await run_scenario(
        "project_analysis",
        "llama3.1:latest",
        "Analyze this project and tell me what frameworks it uses",
        {"authorized": True, "approval_granted": True},
        timeout=180,
    )
    results.append(r)
    print(f"[baseline] project_analysis: {r['total_duration']}s, llm={r.get('llm_calls')}, tools={r.get('tool_calls')}, rounds={r.get('tool_rounds')}")
    
    # Scenario 3: Simple chat (no tools)
    r = await run_scenario(
        "simple_chat",
        "llama3.1:latest",
        "Hello, what can you do?",
        {"authorized": True, "approval_granted": True},
        timeout=120,
    )
    results.append(r)
    print(f"[baseline] simple_chat: {r['total_duration']}s, llm={r.get('llm_calls')}, tools={r.get('tool_calls')}, rounds={r.get('tool_rounds')}")
    
    # Scenario 4: DeepSeek file creation
    r = await run_scenario(
        "deepseek_file_create",
        "deepseek-coder-v2:latest",
        "Create a file called test.py with content 'print(\"hello\")'",
        {"authorized": True, "approval_granted": True},
        timeout=180,
    )
    results.append(r)
    print(f"[baseline] deepseek_file_create: {r['total_duration']}s, llm={r.get('llm_calls')}, tools={r.get('tool_calls')}, rounds={r.get('tool_rounds')}")
    
    # Write results
    out_path = Path(__file__).parent.parent.parent / "FASE_S1_BASELINE.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[baseline] Results written to {out_path}")
    
    # Print summary
    print("\n=== BASELINE SUMMARY ===")
    for r in results:
        print(f"{r['scenario']:25s} | {r.get('model','?'):25s} | {r.get('total_duration',0):6.1f}s | llm={r.get('llm_calls',0):2d} | tools={r.get('tool_calls',0):2d} | rounds={r.get('tool_rounds',0):2d} | status={r.get('final_status','?')}")


if __name__ == "__main__":
    asyncio.run(baseline())
