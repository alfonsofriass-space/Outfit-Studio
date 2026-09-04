"""Add the inspiration lane: proposal sets and a per-user operation lease.

Proponer tres outfits no crea tres análisis: se guarda una única fila por petición y
solo la propuesta elegida promociona a outfit. La referencia va de ``outfits`` hacia
``proposal_sets`` para que una segunda propuesta del mismo conjunto también pueda
generarse más tarde sin volver a pagar. La reserva por usuario conserva el invariante
de que un doble clic no puede pagar dos veces, que ``regeneration_leases`` no puede
cubrir porque su clave primaria es un outfit que todavía no existe.

Revision ID: 20260903_0009
Revises: 20260828_0008
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0009"
down_revision: str | None = "20260828_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposal_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("situation", sa.Text(), nullable=False),
        sa.Column("proposals_json", sa.Text(), nullable=False),
        sa.Column("text_model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_estimate", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_proposal_sets_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposal_sets_owner_id", "proposal_sets", ["owner_id"])

    op.create_table(
        "user_operation_leases",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # batch_alter_table: SQLite no permite añadir una restricción in place y recrea
    # la tabla copiando los datos existentes. Los outfits históricos quedan con
    # ambas columnas a NULL, que es exactamente "no vino de una propuesta".
    with op.batch_alter_table("outfits", schema=None) as batch_op:
        batch_op.add_column(sa.Column("proposal_set_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("proposal_index", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_outfits_proposal_set_id_proposal_sets",
            "proposal_sets",
            ["proposal_set_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_outfits_proposal_set_id_proposal_index",
            ["proposal_set_id", "proposal_index"],
        )
        batch_op.create_index("ix_outfits_proposal_set_id", ["proposal_set_id"])


def downgrade() -> None:
    with op.batch_alter_table("outfits", schema=None) as batch_op:
        batch_op.drop_index("ix_outfits_proposal_set_id")
        batch_op.drop_constraint(
            "uq_outfits_proposal_set_id_proposal_index",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_outfits_proposal_set_id_proposal_sets",
            type_="foreignkey",
        )
        batch_op.drop_column("proposal_index")
        batch_op.drop_column("proposal_set_id")

    op.drop_table("user_operation_leases")
    op.drop_index("ix_proposal_sets_owner_id", table_name="proposal_sets")
    op.drop_table("proposal_sets")
