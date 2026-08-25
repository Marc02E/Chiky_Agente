"""FASE O — Evidence chain and response validation.

Prevents the LLM from claiming success without tool evidence.
Every mutating tool execution is tracked, and the final response
is validated against evidence before being returned to the user.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Tools that mutate state (create, write, modify, delete, execute)
_MUTATING_TOOLS: frozenset[str] = frozenset({
    "create_file", "write_file", "modify_file", "create_directory",
    "create_project", "execute_command", "file_delete", "file_copy",
})

# Patterns that indicate success claims in LLM output
_SUCCESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:created|written|modified|deleted|copied|fixed|completed|done|successfully)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:archivo|carpeta|proyecto)\s+(?:creado|modificado|eliminado|copiado)\b",
        re.IGNORECASE,
    ),
)


@dataclass
class EvidenceRecord:
    """Record of a single tool execution."""
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    verified: bool
    timestamp: float = 0.0


@dataclass
class EvidenceTracker:
    """Tracks tool executions as evidence for claims in the response.

    FASE O: Backend enforcement — the LLM cannot claim success
    without corresponding tool evidence.
    """
    records: list[EvidenceRecord] = field(default_factory=list)

    def record_execution(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        verified: bool = False,
        timestamp: float = 0.0,
    ) -> None:
        """Record a tool execution as evidence."""
        self.records.append(EvidenceRecord(
            tool_name=tool_name,
            args=args,
            result=result,
            verified=verified,
            timestamp=timestamp,
        ))

    def has_evidence(self, tool_name: str) -> bool:
        """Check if any successful execution of this tool exists."""
        return any(
            r.tool_name == tool_name
            and "error" not in r.result
            and not r.result.get("verification_failed")
            for r in self.records
        )

    def has_file_evidence(self, file_path: str) -> bool:
        """Check if a specific file was created/modified successfully."""
        for r in self.records:
            if r.tool_name in ("create_file", "write_file", "modify_file"):
                r_path = r.args.get("file_path") or r.args.get("path") or ""
                if r_path and file_path in r_path or r_path in file_path:
                    if "error" not in r.result and not r.result.get("verification_failed"):
                        return True
        return False

    def has_command_evidence(self, command_substring: str) -> bool:
        """Check if a command was executed successfully."""
        for r in self.records:
            if r.tool_name == "execute_command":
                r_cmd = r.args.get("command", "")
                if command_substring in r_cmd:
                    if r.result.get("exit_code", -1) == 0:
                        return True
        return False

    def has_project_evidence(self, project_path: str) -> bool:
        """Check if a project was created successfully."""
        for r in self.records:
            if r.tool_name == "create_project":
                r_path = r.args.get("base_path") or r.args.get("path") or ""
                if r_path and project_path in r_path or r_path in project_path:
                    if "error" not in r.result:
                        return True
        return False

    def get_evidence_summary(self) -> dict[str, Any]:
        """Return a compact summary of all evidence."""
        return {
            "total_executions": len(self.records),
            "tools_used": list({r.tool_name for r in self.records}),
            "successful": sum(
                1 for r in self.records
                if "error" not in r.result and not r.result.get("verification_failed")
            ),
            "failed": sum(
                1 for r in self.records
                if "error" in r.result or r.result.get("verification_failed")
            ),
        }


@dataclass
class ValidationResult:
    """Result of response validation against evidence."""
    valid: bool
    warnings: list[str] = field(default_factory=list)
    disclaimers: list[str] = field(default_factory=list)


def validate_response(
    response: str,
    evidence: EvidenceTracker,
) -> ValidationResult:
    """Validate that success claims in the response are backed by evidence.

    FASE O: Backend enforcement — scans the response for success claims
    and checks if corresponding tool evidence exists.

    Args:
        response: The LLM's final response text.
        evidence: The evidence tracker with all tool executions.

    Returns:
        ValidationResult with warnings and disclaimers.
    """
    warnings: list[str] = []
    disclaimers: list[str] = []

    # Check for success claims
    for pattern in _SUCCESS_PATTERNS:
        matches = pattern.findall(response)
        if not matches:
            continue

        for match in matches:
            claim_lower = match.lower()

            # Check file creation claims
            if any(w in claim_lower for w in ("created", "creado", "written", "escrito")):
                # Look for file evidence
                file_evidence = any(
                    r.tool_name in ("create_file", "write_file", "create_project")
                    and "error" not in r.result
                    and not r.result.get("verification_failed")
                    for r in evidence.records
                )
                if not file_evidence and evidence.records:
                    warnings.append(f"Claim '{match}' without file creation evidence")
                    disclaimers.append(
                        f"⚠ Note: '{match}' could not be verified programmatically."
                    )

            # Check command execution claims
            elif any(w in claim_lower for w in ("executed", "ejecutado", "run", "corriendo")):
                cmd_evidence = any(
                    r.tool_name == "execute_command"
                    and r.result.get("exit_code", -1) == 0
                    for r in evidence.records
                )
                if not cmd_evidence and evidence.records:
                    warnings.append(f"Claim '{match}' without command execution evidence")
                    disclaimers.append(
                        f"⚠ Note: '{match}' could not be verified programmatically."
                    )

            # Check fix claims
            elif any(w in claim_lower for w in ("fixed", "corregido", "repaired")):
                fix_evidence = any(
                    r.tool_name in ("modify_file", "write_file")
                    and "error" not in r.result
                    and not r.result.get("verification_failed")
                    for r in evidence.records
                )
                if not fix_evidence and evidence.records:
                    warnings.append(f"Claim '{match}' without fix evidence")
                    disclaimers.append(
                        f"⚠ Note: '{match}' could not be verified programmatically."
                    )

    # Deduplicate disclaimers
    unique_disclaimers = list(dict.fromkeys(disclaimers))

    if warnings:
        logger.warning(
            "Response validation found %d unsupported claims: %s",
            len(warnings), warnings,
        )

    return ValidationResult(
        valid=len(warnings) == 0,
        warnings=warnings,
        disclaimers=unique_disclaimers,
    )
