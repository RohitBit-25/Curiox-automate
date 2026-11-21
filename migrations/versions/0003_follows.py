"""follow tracking on creators and leads

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for t in ("creators", "leads"):
        op.add_column(t, sa.Column("followed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(t, sa.Column("followed_back", sa.Boolean(), nullable=True))


def downgrade() -> None:
    for t in ("creators", "leads"):
        op.drop_column(t, "followed_back")
        op.drop_column(t, "followed_at")
