"""Assign every outfit to a user.

Revision ID: 20260809_0007
Revises: 20260809_0006
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0007"
down_revision: str | None = "20260809_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("outfits") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_outfits_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.execute(
        """
        UPDATE outfits
        SET owner_id = (
            SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1
        )
        WHERE owner_id IS NULL
        """
    )
    op.create_index("ix_outfits_owner_id", "outfits", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_outfits_owner_id", table_name="outfits")
    with op.batch_alter_table("outfits") as batch_op:
        batch_op.drop_constraint("fk_outfits_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
