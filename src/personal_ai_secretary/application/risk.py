"""FASE M.2 — Intelligent risk classifier.

Replaces naive substring matching with structural analysis:
- Word-boundary matching (no more "pay" inside "payday")
- Code-generation intent detection (writing code ≠ executing dangerous ops)
- Destination-aware analysis (file paths, system directories)
- Preserves all CRITICAL/HIGH for genuinely dangerous operations
"""

from __future__ import annotations

import re

from personal_ai_secretary.domain.contracts import RiskLevel

# ---------------------------------------------------------------------------
# CRITICAL — always blocked, no context helps
# ---------------------------------------------------------------------------

CRITICAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdrop\s+(the\s+)?database\b", re.IGNORECASE),
    re.compile(r"\bformat\s+(the\s+)?disk\b", re.IGNORECASE),
    re.compile(
        r"\bshutdown\s+(the\s+)?(computer|server|system)\b", re.IGNORECASE,
    ),
    re.compile(r"\berase\s+(all|the|everything)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bwipe\s+(all|the|everything)\b", re.IGNORECASE),
    re.compile(
        r"\bdelete\s+(all|everything|the)\s+"
        r"(files?|data|databases?|directories?)\b",
        re.IGNORECASE,
    ),
)

# ---------------------------------------------------------------------------
# HIGH — dangerous operations (word-boundary matched)
# ---------------------------------------------------------------------------

_HIGH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsend\s+(an?\s+)?email\b", re.IGNORECASE),
    re.compile(r"\bsend\s+(a\s+)?message\b", re.IGNORECASE),
    re.compile(r"\bpublish\b", re.IGNORECASE),
    re.compile(r"\bpost\s+to\b", re.IGNORECASE),
    re.compile(r"\btransfer\b", re.IGNORECASE),
    re.compile(r"\bpay\b", re.IGNORECASE),
    re.compile(r"\boverwrite\b", re.IGNORECASE),
    re.compile(r"\boverride\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+\w+", re.IGNORECASE),  # Generic "delete <something>"
)

# Direct filesystem/command destruction patterns (HIGH even in code-gen context
# if targeting system paths)
DANGEROUS_DESTINATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(C:\\Windows|C:\\Program\s*Files|/etc/|/usr/|/bin/|/sbin/)"),
    re.compile(r"(System32|WinSxS)"),
    re.compile(r"\.ssh|\.gnupg|\.aws|\.azure"),
    re.compile(r"\.git(?!ignore)"),
    re.compile(r"(credentials|secrets|\.env\b(?!\.example))"),
)

# ---------------------------------------------------------------------------
# MEDIUM — operations that modify state but are generally safe
# ---------------------------------------------------------------------------

MEDIUM_KEYWORDS: tuple[str, ...] = (
    "schedule",
    "remind",
    "book",
    "save",
    "add to",
)

# Backward-compatible keyword tuples for compliance/policy.py
CRITICAL_KEYWORDS: tuple[str, ...] = (
    "drop database", "format disk", "shutdown", "erase", "rm -rf", "wipe",
    "delete all files", "delete all data", "delete everything",
)

# ---------------------------------------------------------------------------
# Code-generation intent patterns
# When these are present, operations like "delete", "execute", "run"
# refer to CODE the user wants written, not operations to execute now.
# ---------------------------------------------------------------------------

_CODE_GEN_INTENT: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(create|build|make|write|develop|generate)\s+"
        r"(a|an|the|me)?\s*"
        r"(crud|api|app|application|game|project|script|program|"
        r"function|class|module|service|server|client|endpoint|"
        r"route|handler|controller|model|schema|database|table|"
        r"migration|test|cli|tool|widget|component|page|screen|"
        r"layout|dashboard|interface|file)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(crud|api|app|application|game|project|script|program|"
        r"function|class|module|service|server|client|endpoint|"
        r"route|handler|controller|model|schema|database|table|"
        r"migration|test|cli|tool|widget|component|page|screen|"
        r"layout|dashboard|interface)\s+"
        r"(with|using|for|that|which)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(with\s+(fastapi|flask|django|express|react|vue|angular|"
        r"sqlite|postgres|mysql|mongodb|html|css|javascript|"
        r"typescript|python|node))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(in\s+(python|javascript|typescript|java|go|rust|"
        r"c\+\+|ruby))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(add|include|implement|support)\s+(a\s+)?"
        r"(delete|remove|destroy|drop)\s+"
        r"(endpoint|function|method|route|operation|api|handler)\b",
        re.IGNORECASE,
    ),
)


def _has_code_gen_intent(text: str) -> bool:
    """Detect if the user is asking to WRITE code, not EXECUTE operations."""
    return any(p.search(text) for p in _CODE_GEN_INTENT)


def _has_dangerous_destination(text: str) -> bool:
    """Check if the request targets system/critical paths."""
    return any(p.search(text) for p in DANGEROUS_DESTINATION_PATTERNS)


def classify_risk(text: str) -> RiskLevel:
    """Classify risk based on structural analysis of the request.

    Improvements over naive substring matching:
    1. Word-boundary regex (no more "pay" inside "payday")
    2. Code-generation intent detection
    3. Destination-aware analysis
    4. CRITICAL operations always blocked regardless of context
    """
    # CRITICAL: always blocked, no context helps
    for pattern in CRITICAL_PATTERNS:
        if pattern.search(text):
            return RiskLevel.CRITICAL

    # Check for dangerous destinations (system paths, credentials)
    has_danger_dest = _has_dangerous_destination(text)

    # Check for code-generation intent
    has_code_intent = _has_code_gen_intent(text)

    # HIGH: word-boundary matched dangerous operations
    for pattern in _HIGH_PATTERNS:
        if pattern.search(text):
            # If targeting system paths, always HIGH
            if has_danger_dest:
                return RiskLevel.HIGH
            # If it's clearly code generation, downgrade to MEDIUM
            # (user wants code written, not operations executed)
            if has_code_intent:
                continue  # Skip this HIGH pattern, check next
            return RiskLevel.HIGH

    # MEDIUM: state-modifying operations
    for keyword in MEDIUM_KEYWORDS:
        if keyword in text.lower():
            return RiskLevel.MEDIUM

    # If code-gen intent is present but no specific keywords matched,
    # treat as MEDIUM (it's a development task)
    if has_code_intent:
        return RiskLevel.MEDIUM

    return RiskLevel.LOW
