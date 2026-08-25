"""R.18 — System prompt validation."""
from personal_ai_secretary.tools.prompt import build_system_prompt
from personal_ai_secretary.tools.registry import ToolRegistry
from personal_ai_secretary.tools.filesystem import register_filesystem_tools
from personal_ai_secretary.tools.development import register_development_tools
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.datetime_tool import register_datetime_tools
from personal_ai_secretary.tools.project import register_project_tools


def main():
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)
    register_project_tools(reg)

    tool_names = reg.names()
    compact_descs = reg.compact_descriptions()
    prompt = build_system_prompt(tool_names, compact_descs)
    print(f"Prompt length: {len(prompt)} chars")
    print(f"Estimated tokens: {len(prompt) // 4}")

    print("\n=== SECTIONS ===")
    for line in prompt.split("\n"):
        if line.startswith("##"):
            print(f"  {line}")

    print("\n=== TOOLS MENTIONED ===")
    tool_names = reg.names()
    for t in tool_names:
        status = "PRESENT" if t in prompt else "MISSING"
        print(f"  {t}: {status}")

    print("\n=== KEY RULES ===")
    checks = [
        ("No approval bypass", "approval" in prompt.lower()),
        ("Security section", "## Security" in prompt),
        ("Deletion rule", "file_delete" in prompt and "explicitly requested" in prompt),
        ("Planning rule", "plan before" in prompt.lower() or "brief plan" in prompt.lower()),
        ("Evidence rule", "evidence" in prompt.lower()),
        ("Verification", "verify" in prompt.lower()),
        ("No hallucination", "Never claim" in prompt or "never invent" in prompt.lower()),
        ("Final report", "REPORT" in prompt),
    ]
    for name, present in checks:
        label = "PRESENT" if present else "MISSING"
        print(f"  {name}: {label}")

    missing_tools = [t for t in tool_names if t not in prompt]
    missing_rules = [n for n, p in checks if not p]

    print(f"\n=== VERDICT ===")
    if missing_tools:
        print(f"FAIL: {len(missing_tools)} tools missing from prompt: {missing_tools}")
    elif missing_rules:
        print(f"FAIL: {len(missing_rules)} rules missing: {missing_rules}")
    else:
        print("PASS: All tools present, all key rules present")

    print(f"\n=== FIRST 1500 CHARS ===")
    print(prompt[:1500])


if __name__ == "__main__":
    main()
