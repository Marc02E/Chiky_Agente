from personal_ai_secretary.application.risk import classify_risk
from personal_ai_secretary.domain.contracts import RiskLevel


def test_classify_risk_returns_low_for_benign_input() -> None:
    assert classify_risk("hello") == RiskLevel.LOW
    assert classify_risk("execute me") == RiskLevel.LOW


def test_classify_risk_detects_medium_actions() -> None:
    assert classify_risk("schedule a meeting") == RiskLevel.MEDIUM
    assert classify_risk("remind me to call") == RiskLevel.MEDIUM


def test_classify_risk_detects_high_actions() -> None:
    assert classify_risk("send email to the board") == RiskLevel.HIGH
    assert classify_risk("delete the production database") == RiskLevel.HIGH
    assert classify_risk("publish the report") == RiskLevel.HIGH


def test_classify_risk_detects_critical_actions() -> None:
    assert classify_risk("drop database and recreate it") == RiskLevel.CRITICAL
    assert classify_risk("shutdown the server") == RiskLevel.CRITICAL
    assert classify_risk("rm -rf everything") == RiskLevel.CRITICAL


def test_classify_risk_is_case_insensitive() -> None:
    assert classify_risk("SEND EMAIL to the client") == RiskLevel.HIGH
