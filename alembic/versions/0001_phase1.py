"""phase 1 business tables

Revision ID: 0001_phase1
Revises:
Create Date: 2026-11-22
"""

from alembic import op

from ase.db.orm import Base

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
