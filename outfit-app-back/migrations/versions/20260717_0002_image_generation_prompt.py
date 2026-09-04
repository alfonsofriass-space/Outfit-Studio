"""Store the exact prompt used for each generated image.

Revision ID: 20260717_0002
Revises: 20260716_0001
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("generation_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_column("generation_prompt")
