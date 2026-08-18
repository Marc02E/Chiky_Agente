from personal_ai_secretary.domain.contracts import RiskLevel

CRITICAL_KEYWORDS: tuple[str, ...] = (
    "drop database",
    "format disk",
    "shutdown",
    "erase",
    "rm -rf",
    "wipe",
)

HIGH_KEYWORDS: tuple[str, ...] = (
    "send email",
    "send message",
    "publish",
    "post to",
    "transfer",
    "pay",
    "delete",
    "overwrite",
    "override",
    "execute command",
    "run command",
)

MEDIUM_KEYWORDS: tuple[str, ...] = (
    "schedule",
    "remind",
    "book",
    "create",
    "save",
    "add to",
)


def classify_risk(text: str) -> RiskLevel:
    lowered = text.lower()
    if any(keyword in lowered for keyword in CRITICAL_KEYWORDS):
        return RiskLevel.CRITICAL
    if any(keyword in lowered for keyword in HIGH_KEYWORDS):
        return RiskLevel.HIGH
    if any(keyword in lowered for keyword in MEDIUM_KEYWORDS):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
