"""Allow repeating a product search, keeping every paid attempt.

Cada intento es una llamada pagada, así que se conserva como fila propia en vez de
sobrescribir la anterior. Las búsquedas existentes pasan a ser el intento 1.

Revision ID: 20260828_0008
Revises: 20260809_0007
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0008"
down_revision: str | None = "20260809_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table: SQLite no permite alterar restricciones in place y recrea
    # la tabla copiando los datos existentes.
    with op.batch_alter_table("product_searches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
        batch_op.drop_constraint(
            "uq_product_searches_outfit_id_item_index",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_product_searches_outfit_id_item_index_attempt",
            ["outfit_id", "item_index", "attempt"],
        )


def downgrade() -> None:
    # Solo el intento más reciente puede sobrevivir a la restricción antigua.
    op.execute(
        """
        DELETE FROM product_searches
        WHERE id NOT IN (
            SELECT MAX(id) FROM product_searches GROUP BY outfit_id, item_index
        )
        """
    )
    with op.batch_alter_table("product_searches", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_product_searches_outfit_id_item_index_attempt",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_product_searches_outfit_id_item_index",
            ["outfit_id", "item_index"],
        )
        batch_op.drop_column("attempt")
