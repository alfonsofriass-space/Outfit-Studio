"""Create the current Outfit MVP schema.

Revision ID: 20260716_0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outfits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_description", sa.Text(), nullable=False),
        sa.Column("outfit_json", sa.Text(), nullable=False),
        sa.Column("image_prompt", sa.Text(), nullable=True),
        sa.Column("text_model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outfit_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("image_model", sa.String(length=64), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("size", sa.String(length=16), nullable=False),
        sa.Column("cost_estimate", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["outfit_id"], ["outfits.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_images_outfit_id", "images", ["outfit_id"], unique=False)
    op.create_table(
        "regeneration_leases",
        sa.Column("outfit_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["outfit_id"], ["outfits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("outfit_id"),
    )


def downgrade() -> None:
    op.drop_table("regeneration_leases")
    op.drop_index("ix_images_outfit_id", table_name="images")
    op.drop_table("images")
    op.drop_table("outfits")
