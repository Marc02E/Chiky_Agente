from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualityGate:
    name: str
    minimum_score: float

    def evaluate(self, score: float) -> bool:
        return 0.0 <= score <= 1.0 and score >= self.minimum_score


DEFAULT_GATES = (
    QualityGate("workflow_success", 0.95),
    QualityGate("security_pass", 1.0),
    QualityGate("regression_pass", 1.0),
)


def release_ready(scores: dict[str, float]) -> bool:
    return all(gate.evaluate(scores.get(gate.name, -1.0)) for gate in DEFAULT_GATES)
