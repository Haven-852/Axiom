"""phase 1.2 recovery columns

Revision ID: 0002_phase12
Revises: 0001_phase1
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_phase12"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def _names(kind: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    if kind == "columns":
        return {item["name"] for item in inspector.get_columns(table)}
    if kind == "indexes":
        return {item["name"] for item in inspector.get_indexes(table)}
    return {item["name"] for item in inspector.get_unique_constraints(table) if item.get("name")}


def upgrade() -> None:
    execution_columns = _names("columns", "executions")
    if "conversation_id" not in execution_columns:
        op.add_column("executions", sa.Column("conversation_id", sa.String(64), nullable=True))
    if "container_id" not in execution_columns:
        op.add_column("executions", sa.Column("container_id", sa.String(80), nullable=True))
    if "operation_id" not in execution_columns:
        op.add_column("executions", sa.Column("operation_id", sa.String(160), nullable=True))
    if "attempt" not in execution_columns:
        op.add_column("executions", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    execution_indexes = _names("indexes", "executions")
    if "ix_executions_conversation" not in execution_indexes:
        op.create_index("ix_executions_conversation", "executions", ["conversation_id"])
    if "ix_executions_operation" not in execution_indexes:
        op.create_index("ix_executions_operation", "executions", ["operation_id"])
    result_columns = _names("columns", "test_results")
    if "passed_count" not in result_columns:
        op.add_column("test_results", sa.Column("passed_count", sa.Integer(), nullable=True))
    if "failed_count" not in result_columns:
        op.add_column("test_results", sa.Column("failed_count", sa.Integer(), nullable=True))
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "execution_operations" not in tables:
        op.create_table(
            "execution_operations",
            sa.Column("id", sa.String(160), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("workflow_id", sa.String(36), nullable=False),
            sa.Column("task_id", sa.String(36), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("execution_id", sa.String(36), nullable=True),
            sa.Column("conversation_id", sa.String(64), nullable=True),
            sa.Column("container_id", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workflow_id", "task_id", "kind", "attempt", name="uq_operation_attempt"),
        )
        op.create_index("ix_operations_workflow", "execution_operations", ["workflow_id"])
    if "model_usages" not in tables:
        op.create_table(
            "model_usages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("workflow_id", sa.String(36), nullable=False),
            sa.Column("task_id", sa.String(36), nullable=True),
            sa.Column("execution_id", sa.String(36), nullable=True),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.Column("latency_seconds", sa.Float(), nullable=True),
            sa.Column("estimated_cost", sa.Float(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_model_usages_project", "model_usages", ["project_id"])
        op.create_index("ix_model_usages_workflow", "model_usages", ["workflow_id"])
        op.create_index("ix_model_usages_model", "model_usages", ["model"])
    if "uq_approvals_workflow" not in _names("constraints", "approvals"):
        op.create_unique_constraint("uq_approvals_workflow", "approvals", ["workflow_id"])


def downgrade() -> None:
    op.drop_constraint("uq_approvals_workflow", "approvals", type_="unique")
    op.drop_table("model_usages")
    op.drop_table("execution_operations")
    op.drop_column("test_results", "failed_count")
    op.drop_column("test_results", "passed_count")
    op.drop_column("executions", "attempt")
    op.drop_column("executions", "operation_id")
    op.drop_column("executions", "container_id")
    op.drop_column("executions", "conversation_id")
