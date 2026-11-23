from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DEVELOPING = "developing"
    TESTING = "testing"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    EVALUATING = "evaluating"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESEARCHED = "researched"
    PLANNED = "planned"


class ExecutionStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PENDING = "queued"
    SUCCEEDED = "completed"
    TIMED_OUT = "timeout"


class EventSource(StrEnum):
    ASE = "ase"
    LANGGRAPH = "langgraph"
    OPENHANDS = "openhands"
    DOCKER = "docker"


class DomainModel(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()


class Project(DomainModel):
    name: str
    repo_path: str
    base_ref: str = "HEAD"
    description: str = ""


class Requirement(DomainModel):
    project_id: str
    title: str
    statement: str


class RequirementAnalysis(DomainModel):
    requirement_id: str
    summary: str
    acceptance: list[str]
    risks: list[str] = Field(default_factory=list)


class Task(DomainModel):
    plan_id: str
    title: str
    instruction: str
    status: TaskStatus = TaskStatus.PENDING
    agent_id: str


class TaskDependency(DomainModel):
    plan_id: str
    predecessor_id: str
    successor_id: str


class DevelopmentPlan(DomainModel):
    requirement_id: str
    summary: str
    tasks: list[Task]
    dependencies: list[TaskDependency] = Field(default_factory=list)


class Approval(DomainModel):
    project_id: str
    workflow_id: str
    subject: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    comment: str = ""


class Execution(DomainModel):
    project_id: str
    workflow_id: str
    task_id: str
    agent_id: str
    status: ExecutionStatus = ExecutionStatus.CREATED
    session_id: str | None = None
    workspace_id: str | None = None
    conversation_id: str | None = None
    container_id: str | None = None
    operation_id: str | None = None
    attempt: int = 1
    error: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _legacy_status(cls, value: object) -> object:
        mapping = {"succeeded": "completed", "timed_out": "timeout", "pending": "queued"}
        if isinstance(value, str):
            return mapping.get(value, value)
        return value


class ExecutionEvent(DomainModel):
    project_id: str
    workflow_id: str
    task_id: str | None = None
    execution_id: str | None = None
    event_type: str
    source: EventSource
    payload: dict[str, Any] = Field(default_factory=dict)


class Artifact(DomainModel):
    project_id: str
    workflow_id: str
    execution_id: str | None = None
    kind: str
    uri: str
    body: str = ""


class TestResult(DomainModel):
    execution_id: str
    passed: bool
    command: list[str]
    exit_code: int
    output: str
    duration_seconds: float = 0
    passed_count: int | None = None
    failed_count: int | None = None


class EvaluationReport(DomainModel):
    workflow_id: str
    passed: bool
    summary: str
    findings: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    agent_id: str
    name: str
    agent_type: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    runtime: str
    model: str
    tool_permissions: list[str]
    timeout_seconds: int
    retry_limit: int
    version: str = "1"
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    max_retries: int = 0


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class AgentRun(DomainModel):
    workflow_id: str
    project_id: str
    task_id: str | None = None
    agent_name: str
    agent_version: str
    status: AgentRunStatus = AgentRunStatus.PENDING
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    output_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentContext(BaseModel):
    workflow_id: str
    agent_run_id: str
    project_id: str
    task_id: str | None = None
    correlation_id: str
    input_artifact_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def bind_cancellation(self, cancellation: Any) -> None:
        object.__setattr__(self, "_cancellation", cancellation)

    async def is_cancelled(self) -> bool:
        cancellation = getattr(self, "_cancellation", None)
        if cancellation is None:
            return False
        return await cancellation.is_cancelled()

    async def raise_if_cancelled(self) -> None:
        cancellation = getattr(self, "_cancellation", None)
        if cancellation is not None:
            await cancellation.raise_if_cancelled()


class AgentResult(BaseModel):
    agent_name: str
    agent_version: str
    workflow_id: str
    agent_run_id: str
    status: AgentRunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class WorkspaceInfo(BaseModel):
    workspace_id: str
    project_id: str
    task_id: str
    path: str
    branch: str
    base_ref: str


class DiagnosisResult(BaseModel):
    cause: str
    repair_plan: str
    source: str


class AgentTaskRequest(BaseModel):
    task_id: str
    instruction: str
    workspace_path: str
    timeout_seconds: int = 600
    agent_host: str = ""
    conversation_id: str = ""


class OperationRecord(DomainModel):
    project_id: str
    workflow_id: str
    task_id: str
    kind: str
    attempt: int
    status: str
    execution_id: str | None = None
    conversation_id: str | None = None
    container_id: str | None = None


class ModelUsage(DomainModel):
    project_id: str
    workflow_id: str
    task_id: str | None = None
    execution_id: str | None = None
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_seconds: float | None = None
    estimated_cost: float | None = None
    error: str | None = None
    agent_name: str | None = None
    agent_run_id: str | None = None


class AgentSession(BaseModel):
    session_id: str
    workspace_path: str
    runtime: str


class AgentRunResult(BaseModel):
    session_id: str
    status: ExecutionStatus
    summary: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    conversation_id: str | None = None
    usage: dict[str, Any] | None = None


class CommandPolicy(BaseModel):
    allowed: bool
    reason: str = ""
