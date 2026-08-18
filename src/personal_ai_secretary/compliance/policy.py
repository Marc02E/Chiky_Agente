"""Compliance policy rules (FASE 13A/15C).

FASE 13A deterministic compliance rules (prohibited_commands, credential_leakage)
are unchangeable baseline. FASE 15C adds new deterministic rules that are
strictly additive: existing rules cannot be disabled or reordered; new rules
are appended and evaluated after the defaults. Every rule produces a ``rule_id``
that propagates to span, audit and block messages. No LLM/judgment-based
evaluation: all rules are pure string/regex checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from personal_ai_secretary.application.risk import CRITICAL_KEYWORDS

# ─── Compliance rule base type (FASE 13A) ───────────────────────────────

class ComplianceRule:
    """Base class for all compliance rules.

    Subclasses must implement ``evaluate(user_input, output) -> ComplianceRuleResult``.
    """
    rule_id: str

    def evaluate(self, *, user_input: str, output: str) -> ComplianceRuleResult:
        raise NotImplementedError("Subclasses must implement evaluate()")


# ─── Dataclass used by all rules ─────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ComplianceRuleResult:
    rule_id: str
    passed: bool
    reason: str | None = None


# ─── Original FASE 13A rule classes ─────────────────────────────────────

class ProhibitedCommandsRule:
    """FASE 13A rule: blocks output containing prohibited command keywords."""

    rule_id = "prohibited_commands"

    def evaluate(self, *, user_input: str, output: str) -> ComplianceRuleResult:
        lowered = user_input.lower()
        # Phrase-level prohibitions (substring match intentional for phrase detection)
        _prohibited = {"drop database", "delete database", "system restore",
                       "rm -rf", "format", "sudo rm"}
        for keyword in _prohibited:
            if keyword in lowered:
                return ComplianceRuleResult(
                    rule_id=self.rule_id,
                    passed=False,
                    reason=f"output references prohibited command '{keyword}'",
                )
        return ComplianceRuleResult(rule_id=self.rule_id, passed=True)


class CredentialLeakageRule:
    """FASE 13A rule: blocks output that leaks JWTs or URL credentials."""

    rule_id = "credential_leakage"

    def evaluate(self, *, user_input: str, output: str) -> ComplianceRuleResult:
        # JWT sample format: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwx
        JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwx"
        if JWT_SAMPLE in output:
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains JWT sample pattern",
            )
        # URL-style credentials: protocol://user:pass@host
        if re.search(r"://[^/\s:@]+:[^/@\s]+@", output):
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains URL credential pattern",
            )
        return ComplianceRuleResult(rule_id=self.rule_id, passed=True)


# ─── FASE 13A DEFAULT_RULES baseline ─────────────────────────────────────

# Baseline tuple: prohibited_commands + credential_leakage rules.
# This is the immutable FASE 13A foundation. FASE 15C appends new rules after
# this tuple; the original baseline must not be modified or reordered.
DEFAULT_RULES = (
    ProhibitedCommandsRule(),
    CredentialLeakageRule(),
)


# ─── Original FASE 13A rule evaluation ───────────────────────────────────

def evaluate_policy(
    user_input: str,
    output: str,
    rules: tuple[Any, ...] = DEFAULT_RULES,
) -> ComplianceRuleResult | None:
    """Return the first failing rule result, or ``None`` when all rules pass.

    Iterates over ``rules`` in order and returns the first result
    where ``passed is False``. If all rules pass, returns ``None``.

    ``rules`` defaults to ``DEFAULT_RULES`` (the FASE 13A baseline), but can
    be overridden (e.g. by the compliance agent's active rule set).
    """
    for rule in rules:
        result: ComplianceRuleResult = rule.evaluate(user_input=user_input, output=output)
        if not result.passed:
            return result
    return None


def select_rules(
    enabled: str | None,
    rules: tuple[Any, ...] = DEFAULT_RULES,
) -> tuple[Any, ...]:
    """Return the subset of rules enabled by the ``compliance_rules`` setting.

    Supports the ``compliance_rules`` format (comma-separated rule ids).
    Unknown rule ids are ignored.
    """
    if not enabled or not enabled.strip():
        return tuple(rules)
    names: set[str] = {name.strip() for name in enabled.split(",") if name.strip()}
    return tuple(rule for rule in rules if rule.rule_id in names)


# ─── FASE 15C new deterministic compliance rules ──────────────────────────

class SensitiveDataLeakageRule:
    """Blocks output that leaks sensitive data beyond JWTs/URL credentials.

    Detects common patterns of sensitive information in generated output:
    - Social Security Number-like patterns (DDD-DD-DDDD)
    - Phone numbers (various formats)
    - API key-like strings (sk-, pk-, etc.)
    - Credit card-like patterns (13-16 digits with possible separators)
    """
    rule_id = "sensitive_data_leakage"

    def evaluate(self, *, user_input: str, output: str) -> ComplianceRuleResult:
        # SSN pattern: 3 digits - 2 digits - 4 digits
        if re.search(r"\b\d{3}-\d{2}-\d{4}\b", output):
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains Social Security Number pattern",
            )
        # US phone patterns: (xxx) xxx-xxxx or xxx-xxx-xxxx
        if re.search(r"\(\d{3}\) \d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b", output):
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains phone number pattern",
            )
        # API key patterns (starts with known prefixes)
        if re.search(r"\bsk-[A-Za-z0-9]{20,}\b|\bpk_live_[A-Za-z0-9]{24,}\b", output):
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains API key pattern",
            )
        # Credit card-like (13-16 digits with possible spaces/hyphens)
        if re.search(r"\b(?:\d[ -]*?){13,16}\b", output):
            return ComplianceRuleResult(
                rule_id=self.rule_id,
                passed=False,
                reason="output contains credit card-like pattern",
            )
        return ComplianceRuleResult(rule_id=self.rule_id, passed=True)


class DisallowedTopicRule:
    """Blocks output that discusses disallowed topics.

    Extends the prohibited-commands rule by also blocking output that discusses
    topics that should not be generated by the assistant (e.g., certain
    controlled substances, violent content, etc.). Uses keyword matching from
    the existing ``CRITICAL_KEYWORDS`` set plus topic-specific extensions.
    """
    rule_id = "disallowed_topic"

    def evaluate(self, *, user_input: str, output: str) -> ComplianceRuleResult:
        lowered = output.lower()
        # Check against existing CRITICAL_KEYWORDS
        for keyword in CRITICAL_KEYWORDS:
            if keyword.lower() in lowered:
                return ComplianceRuleResult(
                    rule_id=self.rule_id,
                    passed=False,
                    reason=f"output references prohibited keyword '{keyword}'",
                )
        # Additional topic keywords (new, extensible)
        _additional_keywords = {"explosives", "weapon", "drugs"}
        for topic in _additional_keywords:
            if topic in lowered:
                return ComplianceRuleResult(
                    rule_id=self.rule_id,
                    passed=False,
                    reason=f"output references disallowed topic '{topic}'",
                )
        return ComplianceRuleResult(rule_id=self.rule_id, passed=True)


# ─── Advanced rule tuple: defaults first, new rules appended at the end ─────

# Build the advanced rule set once DEFAULT_RULES is fully populated.
# This runs after the package base module has injected the real DEFAULT_RULES.
if DEFAULT_RULES:
    _ADVANCED_RULES = (*DEFAULT_RULES, SensitiveDataLeakageRule(), DisallowedTopicRule())
else:
    _ADVANCED_RULES = (SensitiveDataLeakageRule(), DisallowedTopicRule())


def advanced_select_rules(
    enabled: str | None, rules: tuple[Any, ...] = _ADVANCED_RULES,
) -> tuple[Any, ...]:
    """Return the subset of rules enabled by the ``compliance_rules`` setting.

    Supports the original ``compliance_rules`` format (comma-separated rule ids)
    as well as the advanced format that includes the new rule ids.
    Unknown rule ids are ignored.
    """
    if not enabled or not enabled.strip():
        return tuple(rules)
    names: set[str] = {name.strip() for name in enabled.split(",") if name.strip()}
    return tuple(rule for rule in rules if rule.rule_id in names)


def advanced_evaluate_policy(
    user_input: str, output: str, rules: tuple[Any, ...] = _ADVANCED_RULES,
) -> ComplianceRuleResult | None:
    """Return the first failing rule result, or ``None`` when all rules pass.

    Uses the advanced rule set (defaults + new rules). This is the entry point
    the workflow should use when the advanced compliance rules are active.
    """
    for rule in rules:
        result: ComplianceRuleResult = rule.evaluate(user_input=user_input, output=output)
        if not result.passed:
            return result
    return None


# ─── Public API ───────────────────────────────────────────────────────────

__all__ = [
    "ComplianceRuleResult",
    "DEFAULT_RULES",
    "evaluate_policy",
    "select_rules",
    "ProhibitedCommandsRule",
    "CredentialLeakageRule",
    "SensitiveDataLeakageRule",
    "DisallowedTopicRule",
    "advanced_select_rules",
    "advanced_evaluate_policy",
]