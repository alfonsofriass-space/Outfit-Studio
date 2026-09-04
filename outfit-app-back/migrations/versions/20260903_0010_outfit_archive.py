"""Turn the library into an archive: chosen composition and favourites.

La portada dejaba de ser útil en cuanto había variaciones: se mostraba siempre la
composición original, muchas veces la que el usuario había descartado. ``is_chosen``
permite corregirlo explícitamente e ``is_favourite`` da a la biblioteca un filtro real.

``is_chosen`` vive en ``images`` y no como ``chosen_image_id`` en ``outfits``: esa
segunda forma cerraba un ciclo de claves foráneas con ``images.outfit_id`` que SQLAlchemy
no puede ordenar y que avisa de que será un error en versiones futuras. Aquí no se añade
ninguna clave foránea nueva, y borrar una composición se lleva su marca con ella. El
índice único parcial garantiza como máximo una elegida por outfit.

Revision ID: 20260903_0010
Revises: 20260903_0009
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0010"
down_revision: str | None = "20260903_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los outfits existentes quedan sin composición elegida y sin marcar, que es
    # exactamente su estado real: nadie ha elegido todavía.
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_chosen",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_index(
        "uq_images_chosen_per_outfit",
        "images",
        ["outfit_id"],
        unique=True,
        sqlite_where=sa.text("is_chosen = 1"),
    )

    with op.batch_alter_table("outfits", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_favourite",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("outfits", schema=None) as batch_op:
        batch_op.drop_column("is_favourite")

    op.drop_index("uq_images_chosen_per_outfit", table_name="images")

    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.drop_column("is_chosen")
