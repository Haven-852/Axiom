from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectRow(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    repo_path: Mapped[str] = mapped_column(Text)
    base_ref: Mapped[str] = mapped_column(String(200), default="HEAD")
    description: Mapped[str] = mapped_column(Text, default="")


class RequirementRow(TimestampMixin, Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)


class RequirementAnalysisRow(TimestampMixin, Base):
    __tablename__ = "requirement_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    acceptance: Mapped[list] = mapped_column(JSON)
    risks: Mapped[list] = mapped_column(JSON)


class DevelopmentPlanRow(TimestampMixin, Base):
    __tablename__ = "development_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)


class TaskRow(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("development_plans.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))


class TaskDependencyRow(TimestampMixin, Base):
    __tablename__ = "task_dependencies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("development_plans.id"), index=True)
    predecessor_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    successor_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))


class ApprovalRow(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("workflow_id", "subject", name="uq_approvals_workflow_subject"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    subject: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class WorkflowRow(TimestampMixin, Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"))
    status: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkflowCheckpointRow(Base):
    """Index of LangGraph thread ids. The checkpointer is the restore mechanism."""

    __tablename__ = "workflow_checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(36))
    node: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionRow(TimestampMixin, Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    container_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutionEventRow(Base):
    __tablename__ = "execution_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(TimestampMixin, Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")


class TestResultRow(TimestampMixin, Base):
    __tablename__ = "test_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    command: Mapped[list] = mapped_column(JSON)
    exit_code: Mapped[int] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    passed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EvaluationReportRow(TimestampMixin, Base):
    __tablename__ = "evaluation_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), unique=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)
    findings: Mapped[list] = mapped_column(JSON)


class ExecutionOperationRow(TimestampMixin, Base):
    __tablename__ = "execution_operations"
    __table_args__ = (UniqueConstraint("workflow_id", "task_id", "kind", "attempt", name="uq_operation_attempt"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ModelUsageRow(Base):
    __tablename__ = "model_usages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200), index=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentRunRow(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    input_artifact_ids: Mapped[list] = mapped_column(JSON)
    output_artifact_ids: Mapped[list] = mapped_column(JSON)
    execution_ids: Mapped[list] = mapped_column(JSON)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    output_snapshot: Mapped[dict] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRunExecutionRow(Base):
    __tablename__ = "agent_run_executions"
    __table_args__ = (UniqueConstraint("agent_run_id", "execution_id", name="uq_agent_run_execution"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RepositoryLeaseRow(TimestampMixin, Base):
    __tablename__ = "repository_leases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_key: Mapped[str] = mapped_column(Text, unique=True)
    holder_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AgentRegistryRow(Base):
    __tablename__ = "agent_registry"
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    agent_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64), index=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
