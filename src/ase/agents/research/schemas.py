"""Research report protocol. This is not the Phase 1 planner RequirementAnalysis."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Likelihood(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class CriterionSource(StrEnum):
    USER_REQUIREMENT = "user_requirement"
    EXISTING_CODE = "existing_code"
    TECHNICAL_CONSTRAINT = "technical_constraint"
    INFERRED = "inferred"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementAnalysis(_Strict):
    summary: str = Field(min_length=1, max_length=2000)
    objectives: list[str] = Field(default_factory=list, max_length=12)
    functional_requirements: list[str] = Field(default_factory=list, max_length=20)
    non_functional_requirements: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)

    @field_validator(
        "objectives",
        "functional_requirements",
        "non_functional_requirements",
        "constraints",
        "assumptions",
        "clarification_questions",
    )
    @classmethod
    def _items(cls, value: list[str]) -> list[str]:
        return [_bounded(item, 500) for item in value]


class ProjectAnalysis(_Strict):
    project_summary: str = Field(min_length=1, max_length=2000)
    languages: list[str] = Field(default_factory=list, max_length=8)
    frameworks: list[str] = Field(default_factory=list, max_length=16)
    dependencies: list[str] = Field(default_factory=list, max_length=40)
    architecture_description: str = Field(min_length=1, max_length=2000)
    entry_points: list[str] = Field(default_factory=list, max_length=12)
    test_frameworks: list[str] = Field(default_factory=list, max_length=8)
    database_technologies: list[str] = Field(default_factory=list, max_length=8)
    relevant_modules: list[str] = Field(default_factory=list, max_length=20)


class CodeFinding(_Strict):
    finding_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    file_path: str = Field(min_length=1, max_length=240)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    relevance: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)

    @field_validator("file_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or value.startswith(("/", "\\")):
            raise ValueError("file_path must stay inside the repository")
        return path.as_posix()


class TechnicalRisk(_Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    severity: Severity
    likelihood: Likelihood
    affected_modules: list[str] = Field(default_factory=list, max_length=12)
    mitigation: str = Field(min_length=1, max_length=1000)


class AcceptanceCriterion(_Strict):
    criterion_id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    verification_method: str = Field(min_length=1, max_length=300)
    priority: Priority
    source: CriterionSource


class ResearchAssessment(_Strict):
    requirement_analysis: RequirementAnalysis
    code_findings: list[CodeFinding] = Field(default_factory=list, max_length=20)
    existing_capabilities: list[str] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[str] = Field(default_factory=list, max_length=16)
    technical_recommendations: list[str] = Field(default_factory=list, max_length=12)
    technical_risks: list[TechnicalRisk] = Field(default_factory=list, max_length=12)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=16)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)
    research_summary: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class ResearchReport(_Strict):
    schema_version: str = Field(pattern=r"^1\.0$")
    requirement_analysis: RequirementAnalysis
    project_analysis: ProjectAnalysis
    code_findings: list[CodeFinding] = Field(default_factory=list, max_length=20)
    existing_capabilities: list[str] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[str] = Field(default_factory=list, max_length=16)
    technical_recommendations: list[str] = Field(default_factory=list, max_length=12)
    technical_risks: list[TechnicalRisk] = Field(default_factory=list, max_length=12)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=16)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)
    research_summary: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=16)


def _bounded(value: str, limit: int) -> str:
    text = value.strip()
    if not text or len(text) > limit:
        raise ValueError("text item is empty or too long")
    return text
