"""Allow one approval row per workflow subject.

Revision ID: 0004_phase23
Revises: 0003_phase20
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_phase23"
down_revision = "0003_phase20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "approvals" not in inspector.get_table_names():
        return
    names = {item["name"] for item in inspector.get_unique_constraints("approvals")}
    if "uq_approvals_workflow" in names:
        op.drop_constraint("uq_approvals_workflow", "approvals", type_="unique")
    names = {item["name"] for item in sa.inspect(bind).get_unique_constraints("approvals")}
    if "uq_approvals_workflow_subject" not in names:
        op.create_unique_constraint("uq_approvals_workflow_subject", "approvals", ["workflow_id", "subject"])


def downgrade() -> None:
    op.drop_constraint("uq_approvals_workflow_subject", "approvals", type_="unique")
    op.create_unique_constraint("uq_approvals_workflow", "approvals", ["workflow_id"])
