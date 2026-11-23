"""phase 2.0 agent runs and repository leases

Revision ID: 0003_phase20
Revises: 0002_phase12
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_phase20"
down_revision = "0002_phase12"
branch_labels = None
depends_on = None


def _present(kind: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if kind == "tables":
        return tables
    if table not in tables:
        return set()
    if kind == "columns":
        return {item["name"] for item in inspector.get_columns(table)}
    return {item["name"] for item in inspector.get_indexes(table)}


def upgrade() -> None:
    usage_columns = _present("columns", "model_usages")
    if "agent_name" not in usage_columns:
        op.add_column("model_usages", sa.Column("agent_name", sa.String(64), nullable=True))
    if "agent_run_id" not in usage_columns:
        op.add_column("model_usages", sa.Column("agent_run_id", sa.String(36), nullable=True))
    if "ix_model_usages_agent_run" not in _present("indexes", "model_usages"):
        op.create_index("ix_model_usages_agent_run", "model_usages", ["agent_run_id"])
    tables = _present("tables", "")
    if "agent_runs" not in tables:
        _create_agent_runs()
    if "agent_run_executions" not in tables:
        _create_agent_run_executions()
    if "repository_leases" not in tables:
        _create_repository_leases()


def _create_agent_runs() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("output_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("execution_ids", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency"),
    )
    op.create_index("ix_agent_runs_workflow", "agent_runs", ["workflow_id"])
    op.create_index("ix_agent_runs_task", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_agent_name", "agent_runs", ["agent_name"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])


def _create_agent_run_executions() -> None:
    op.create_table(
        "agent_run_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("executions.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_run_id", "execution_id", name="uq_agent_run_execution"),
    )
    op.create_index("ix_agent_run_executions_run", "agent_run_executions", ["agent_run_id"])
    op.create_index("ix_agent_run_executions_execution", "agent_run_executions", ["execution_id"])


def _create_repository_leases() -> None:
    op.create_table(
        "repository_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_key", sa.Text(), nullable=False),
        sa.Column("holder_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_key", name="uq_repository_leases_key"),
    )
    op.create_index("ix_repository_leases_holder", "repository_leases", ["holder_id"])
    op.create_index("ix_repository_leases_expires", "repository_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_table("repository_leases")
    op.drop_table("agent_run_executions")
    op.drop_table("agent_runs")
    op.drop_index("ix_model_usages_agent_run", table_name="model_usages")
    op.drop_column("model_usages", "agent_run_id")
    op.drop_column("model_usages", "agent_name")
