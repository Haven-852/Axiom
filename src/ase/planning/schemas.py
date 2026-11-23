"""Planning protocol. Rich fields stay here; the task table stores the DAG."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskType(StrEnum):
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    REFACTORING = "refactoring"
    DOCUMENTATION = "documentation"
    INVESTIGATION = "investigation"


class PlanPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Complexity(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class ClarificationStatus(StrEnum):
    UNRESOLVED = "unresolved"
    ANSWERED = "answered"
    DEFERRED = "deferred"


class PlanTask(_Strict):
    task_id: str = Field(min_length=1, max_length=40, description="Stable id unique inside one plan.")
    title: str = Field(min_length=1, max_length=200, description="Short task title.")
    description: str = Field(min_length=1, max_length=2000, description="What the task does and what it must not assume.")
    task_type: TaskType = Field(description="Kind of work. This is not an execution agent.")
    priority: PlanPriority
    dependencies: list[str] = Field(default_factory=list, max_length=12, description="Predecessor task ids.")
    related_files: list[str] = Field(default_factory=list, max_length=12, description="Repository-relative paths.")
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8, description="Checks for this task.")
    estimated_complexity: Complexity
    source_findings: list[str] = Field(default_factory=list, max_length=8, description="ResearchReport finding ids.")
    source_acceptance_criteria: list[str] = Field(default_factory=list, max_length=8, description="ResearchReport criterion ids.")
    assumptions: list[str] = Field(default_factory=list, max_length=8, description="Inferences, not confirmed requirements.")
    blocking_clarification_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("related_files")
    @classmethod
    def _relative_files(cls, value: list[str]) -> list[str]:
        return [_relative(item) for item in value]


class ClarificationItem(_Strict):
    clarification_id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=500, description="Unresolved question copied from research.")
    context: str = Field(min_length=1, max_length=1000)
    impact: str = Field(min_length=1, max_length=500, description="What decision this question blocks.")
    blocking: bool
    affected_task_ids: list[str] = Field(default_factory=list, max_length=12)
    status: ClarificationStatus
    source: str = Field(min_length=1, max_length=64)
    source_reference: str = Field(default="", max_length=200, description="Research question, prior clarification id, or user note.")
    answer: str = Field(default="", max_length=2000)
    answered_by: str = Field(default="", max_length=200)
    answered_at: str = Field(default="", max_length=40)


class PlanRisk(_Strict):
    risk_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    severity: str = Field(pattern=r"^(low|medium|high|critical)$")
    likelihood: str = Field(pattern=r"^(low|medium|high)$")
    mitigation: str = Field(min_length=1, max_length=1000)
    affected_task_ids: list[str] = Field(default_factory=list, max_length=12)
    source: str = Field(min_length=1, max_length=64)


class PlanValidationResult(_Strict):
    valid: bool = Field(description="True only when the DAG has no structural errors.")
    structurally_valid: bool
    errors: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    task_count: int = Field(ge=0)
    blocked_task_count: int = Field(ge=0)
    executable_task_count: int = Field(ge=0)
    topological_order: list[str] = Field(default_factory=list, max_length=40, description="Full structural order, including blocked tasks.")


class PlanningDraft(_Strict):
    planning_summary: str = Field(min_length=1, max_length=2000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=20)
    clarification_items: list[ClarificationItem] = Field(default_factory=list, max_length=12)
    risks: list[PlanRisk] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class Plan(_Strict):
    schema_version: str = Field(pattern=r"^1\.0$", description="Plan schema version.")
    plan_id: str = Field(min_length=1, max_length=40)
    source_research_artifact_id: str
    source_research_agent_run_id: str
    requirement_summary: str = Field(min_length=1, max_length=2000)
    planning_summary: str = Field(min_length=1, max_length=2000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=20)
    clarification_items: list[ClarificationItem] = Field(default_factory=list, max_length=12)
    risks: list[PlanRisk] = Field(default_factory=list, max_length=12)
    execution_order: list[str] = Field(default_factory=list, max_length=20, description="Executable tasks only, in dependency order.")
    validation_result: PlanValidationResult
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=16)


def _relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value.startswith(("/", "\\")):
        raise ValueError("related file must stay inside the repository")
    return path.as_posix()
