from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_secretary.domain.contracts import RiskLevel


class AgentRole(StrEnum):
    PLANNER = "planner"
    RESEARCH = "research"
    EXECUTION = "execution"
    REVIEWER = "reviewer"
    COMPLIANCE = "compliance"


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    session_id: UUID
    user_id: str
    text: str = Field(min_length=1, max_length=20_000)
    correlation_id: str = Field(min_length=1, max_length=128)
    risk_level: RiskLevel = RiskLevel.LOW
    context: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=200)
    requires_evidence: bool = False
    requires_approval: bool = False
    risk_level: RiskLevel = RiskLevel.LOW


class ExecutionPlan(BaseModel):
    request_id: UUID
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=4_000)


class AgentArtifact(BaseModel):
    role: AgentRole
    request_id: UUID
    content: str
    risk_level: RiskLevel
    evidence_ids: list[str] = Field(default_factory=list)
    blocked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class Agent(Protocol):
    role: AgentRole

    async def run(self, data: AgentInput) -> AgentArtifact: ...
