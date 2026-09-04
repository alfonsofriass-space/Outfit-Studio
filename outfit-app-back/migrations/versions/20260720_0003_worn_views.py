"""Store one optional worn view per generated flat-lay.

Revision ID: 20260720_0003
Revises: 20260717_0002
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0003"
down_revision: str | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worn_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_image_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("generation_prompt", sa.Text(), nullable=False),
        sa.Column("image_model", sa.String(length=64), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("size", sa.String(length=16), nullable=False),
        sa.Column("cost_estimate", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_image_id"], ["images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_image_id"),
    )


def downgrade() -> None:
    op.drop_table("worn_views")
