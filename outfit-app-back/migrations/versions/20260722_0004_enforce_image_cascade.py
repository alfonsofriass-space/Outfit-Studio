"""Enforce outfit image cascades under SQLite foreign keys.

Revision ID: 20260722_0004
Revises: 20260720_0003
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260722_0004"
down_revision: str | None = "20260720_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_images_outfit_id_outfits"
NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _replace_image_foreign_key(*, ondelete: str | None) -> None:
    with op.batch_alter_table(
        "images",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.create_foreign_key(
            FK_NAME,
            "outfits",
            ["outfit_id"],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_image_foreign_key(ondelete="CASCADE")


def downgrade() -> None:
    _replace_image_foreign_key(ondelete=None)
