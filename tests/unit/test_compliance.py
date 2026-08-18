from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ComplianceAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.compliance.policy import (
    DEFAULT_RULES,
    CredentialLeakageRule,
    DisallowedTopicRule,
    ProhibitedCommandsRule,
    SensitiveDataLeakageRule,
    advanced_evaluate_policy,
    advanced_select_rules,
    evaluate_policy,
    select_rules,
)
from personal_ai_secretary.domain.contracts import RiskLevel

JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwx"


def _input(text: str = "hello", **context: object) -> AgentInput:
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="user-1",
        text=text,
        correlation_id="corr-compliance",
        context=context,
    )


def test_prohibited_commands_rule_blocks_critical_input() -> None:
    rule = ProhibitedCommandsRule()
    result = rule.evaluate(
        user_input="drop database and recreate it", output="ok"
    )
    assert result.passed is False
    assert result.rule_id == "prohibited_commands"


def test_prohibited_commands_rule_passes_benign_input() -> None:
    rule = ProhibitedCommandsRule()
    assert rule.evaluate(user_input="send email to the board", output="ok").passed
    assert rule.evaluate(user_input="hello", output="ok").passed


def test_credential_leakage_rule_blocks_jwt_output() -> None:
    rule = CredentialLeakageRule()
    result = rule.evaluate(user_input="hello", output=f"reply {JWT_SAMPLE}")
    assert result.passed is False
    assert result.rule_id == "credential_leakage"


def test_credential_leakage_rule_blocks_url_credentials_output() -> None:
    rule = CredentialLeakageRule()
    result = rule.evaluate(
        user_input="hello", output="postgresql://admin:secret@db.example.com/app"
    )
    assert result.passed is False


def test_credential_leakage_rule_passes_plain_output() -> None:
    rule = CredentialLeakageRule()
    assert rule.evaluate(user_input="hello", output="DETERMINISTIC_RESPONSE: hello").passed


def test_evaluate_policy_returns_first_failing_rule() -> None:
    result = evaluate_policy("drop database", "ok")
    assert result is not None
    assert result.rule_id == "prohibited_commands"


def test_evaluate_policy_returns_none_when_all_pass() -> None:
    assert evaluate_policy("send email to the board", "DETERMINISTIC_RESPONSE: fine") is None


def test_select_rules_filters_default_rules() -> None:
    only_credentials = select_rules("credential_leakage")
    assert [rule.rule_id for rule in only_credentials] == ["credential_leakage"]
    assert select_rules("") == DEFAULT_RULES
    assert select_rules("unknown_rule") == ()


@pytest.mark.asyncio
async def test_compliance_agent_blocks_prohibited_input_with_rule_id() -> None:
    artifact = await ComplianceAgent().run(
        _input(
            "drop database and recreate it",
            user_input="drop database and recreate it",
            output="executed",
            producer_role="execution",
        )
    )
    assert artifact.blocked
    assert artifact.metadata["rule_id"] == "prohibited_commands"
    assert "rule" in artifact.content


@pytest.mark.asyncio
async def test_compliance_agent_blocks_credential_leakage_output() -> None:
    artifact = await ComplianceAgent().run(
        _input(
            "hello",
            user_input="hello",
            output=f"here is the token {JWT_SAMPLE}",
            producer_role="execution",
        )
    )
    assert artifact.blocked
    assert artifact.metadata["rule_id"] == "credential_leakage"


@pytest.mark.asyncio
async def test_compliance_agent_passes_normal_request() -> None:
    artifact = await ComplianceAgent().run(
        _input(
            "hello",
            user_input="hello",
            output="DETERMINISTIC_RESPONSE: hello",
            producer_role="execution",
        )
    )
    assert not artifact.blocked
    assert artifact.content == "Compliance passed."


@pytest.mark.asyncio
async def test_compliance_agent_still_blocks_context_policy_violation() -> None:
    artifact = await ComplianceAgent().run(
        _input("hello", policy_violation=True, output="ok")
    )
    assert artifact.blocked
    assert artifact.metadata["policy_violation"] is True
    assert "rule_id" not in artifact.metadata


@pytest.mark.asyncio
async def test_compliance_agent_disabled_rules_pass() -> None:
    artifact = await ComplianceAgent(enabled=False).run(
        _input(
            "drop database",
            user_input="drop database",
            output="executed",
            producer_role="execution",
        )
    )
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_compliance_agent_disabled_overrides_injected_rules() -> None:
    artifact = await ComplianceAgent(
        rules=(ProhibitedCommandsRule(),), enabled=False
    ).run(
        _input(
            "drop database",
            user_input="drop database",
            output="executed",
            producer_role="execution",
        )
    )
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_compliance_agent_blocks_empty_output() -> None:
    artifact = await ComplianceAgent().run(
        _input("hello", user_input="hello", output="  ", producer_role="execution")
    )
    assert artifact.blocked


@pytest.mark.asyncio
async def test_workflow_compliance_hard_gate_blocks_critical_with_approval() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(
        uuid4(),
        uuid4(),
        "user-1",
        "drop database and recreate it",
        "corr-compliance-wf",
        RiskLevel.CRITICAL,
        {"approval_granted": True},
    )

    assert result.status == "blocked"
    compliance = result.artifacts[-1]
    assert compliance.role.value == "compliance"
    assert compliance.blocked
    assert compliance.metadata["rule_id"] == "prohibited_commands"
    assert "prohibited_commands" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_workflow_compliance_metric_and_audit_rule_id() -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    store = InMemoryAuditStore()
    metrics = Metrics.create()
    workflow = GovernedWorkflow(observability=Observability(store, metrics))

    blocked = await workflow.run(
        uuid4(),
        uuid4(),
        "user-1",
        "drop database and recreate it",
        "corr-compliance-metric",
        RiskLevel.CRITICAL,
        {"approval_granted": True},
    )
    assert blocked.status == "blocked"
    assert metrics.snapshot()["compliance_blocks"] == 1

    passed = await workflow.run(
        uuid4(), uuid4(), "user-1", "hello", "corr-compliance-pass"
    )
    assert passed.status == "completed"
    assert metrics.snapshot()["compliance_passes"] == 1

    events = await store.events()
    compliance_block = next(
        e for e in events if e.event_type == "compliance" and e.outcome == "blocked"
    )
    assert compliance_block.details["rule_id"] == "prohibited_commands"


# ─── FASE 15C: SensitiveDataLeakageRule ───────────────────────────────────


def test_sensitive_data_leakage_blocks_ssn() -> None:
    rule = SensitiveDataLeakageRule()
    result = rule.evaluate(user_input="hello", output="my ssn is 123-45-6789")
    assert result.passed is False
    assert result.rule_id == "sensitive_data_leakage"
    assert "Social Security" in result.reason


def test_sensitive_data_leakage_blocks_phone_number() -> None:
    rule = SensitiveDataLeakageRule()
    result = rule.evaluate(user_input="hello", output="call me at (555) 123-4567")
    assert result.passed is False
    assert result.rule_id == "sensitive_data_leakage"
    assert "phone" in result.reason


def test_sensitive_data_leakage_blocks_api_key_pattern() -> None:
    rule = SensitiveDataLeakageRule()
    result = rule.evaluate(
        user_input="hello", output="use sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    assert result.passed is False
    assert result.rule_id == "sensitive_data_leakage"
    assert "API key" in result.reason


def test_sensitive_data_leakage_blocks_credit_card() -> None:
    rule = SensitiveDataLeakageRule()
    result = rule.evaluate(
        user_input="hello", output="card number is 4111-1111-1111-1111"
    )
    assert result.passed is False
    assert result.rule_id == "sensitive_data_leakage"
    assert "credit card" in result.reason


def test_sensitive_data_leakage_passes_benign_output() -> None:
    rule = SensitiveDataLeakageRule()
    result = rule.evaluate(user_input="hello", output="DETERMINISTIC_RESPONSE: hello")
    assert result.passed is True
    assert result.rule_id == "sensitive_data_leakage"


# ─── FASE 15C: DisallowedTopicRule ────────────────────────────────────────


def test_disallowed_topic_blocks_critical_keyword() -> None:
    from personal_ai_secretary.application.risk import CRITICAL_KEYWORDS

    rule = DisallowedTopicRule()
    keyword = list(CRITICAL_KEYWORDS)[0]
    result = rule.evaluate(user_input="hello", output=f"discussing {keyword}")
    assert result.passed is False
    assert result.rule_id == "disallowed_topic"
    assert "prohibited keyword" in result.reason


def test_disallowed_topic_blocks_additional_keyword() -> None:
    rule = DisallowedTopicRule()
    result = rule.evaluate(user_input="hello", output="discussing explosives")
    assert result.passed is False
    assert result.rule_id == "disallowed_topic"
    assert "disallowed topic" in result.reason


def test_disallowed_topic_passes_benign_output() -> None:
    rule = DisallowedTopicRule()
    result = rule.evaluate(
        user_input="hello", output="DETERMINISTIC_RESPONSE: have a nice day"
    )
    assert result.passed is True


# ─── FASE 15C: advanced_select_rules ──────────────────────────────────────


def test_advanced_select_rules_includes_15c_by_default() -> None:
    rules = advanced_select_rules(None)
    rule_ids = [r.rule_id for r in rules]
    assert "prohibited_commands" in rule_ids
    assert "credential_leakage" in rule_ids
    assert "sensitive_data_leakage" in rule_ids
    assert "disallowed_topic" in rule_ids


def test_advanced_select_rules_filters_by_name() -> None:
    rules = advanced_select_rules("sensitive_data_leakage")
    assert [r.rule_id for r in rules] == ["sensitive_data_leakage"]


def test_advanced_select_rules_ignores_unknown() -> None:
    rules = advanced_select_rules("nonexistent_rule")
    assert rules == ()


# ─── FASE 15C: advanced_evaluate_policy ───────────────────────────────────


def test_advanced_evaluate_policy_blocks_ssn_in_output() -> None:
    result = advanced_evaluate_policy("hello", "ssn is 123-45-6789")
    assert result is not None
    assert result.rule_id == "sensitive_data_leakage"


def test_advanced_evaluate_policy_blocks_disallowed_topic() -> None:
    result = advanced_evaluate_policy("hello", "discussing explosives")
    assert result is not None
    assert result.rule_id == "disallowed_topic"


def test_advanced_evaluate_policy_returns_none_when_all_pass() -> None:
    result = advanced_evaluate_policy(
        "hello", "DETERMINISTIC_RESPONSE: have a nice day"
    )
    assert result is None


# ─── FASE 15C: backward compatibility ─────────────────────────────────────


def test_default_rules_do_not_include_15c() -> None:
    """FASE 13A DEFAULT_RULES must not contain 15C rules."""
    rule_ids = [r.rule_id for r in DEFAULT_RULES]
    assert "sensitive_data_leakage" not in rule_ids
    assert "disallowed_topic" not in rule_ids


def test_select_rules_does_not_include_15c_by_default() -> None:
    """The original select_rules uses DEFAULT_RULES (no 15C)."""
    rules = select_rules(None)
    rule_ids = [r.rule_id for r in rules]
    assert "sensitive_data_leakage" not in rule_ids
    assert "disallowed_topic" not in rule_ids