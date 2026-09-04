"""Persist one completed product search per outfit item.

Revision ID: 20260727_0005
Revises: 20260722_0004
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0005"
down_revision: str | None = "20260722_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_searches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outfit_id", sa.Integer(), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("additional_details", sa.Text(), nullable=True),
        sa.Column("candidates_json", sa.Text(), nullable=False),
        sa.Column("search_model", sa.String(length=64), nullable=False),
        sa.Column("web_search_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_estimate", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["outfit_id"], ["outfits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outfit_id",
            "item_index",
            name="uq_product_searches_outfit_id_item_index",
        ),
    )
    op.create_index(
        "ix_product_searches_outfit_id",
        "product_searches",
        ["outfit_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_product_searches_outfit_id", table_name="product_searches")
    op.drop_table("product_searches")
