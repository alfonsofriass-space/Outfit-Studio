from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app import models  # noqa: F401
from app.db import Base, _set_sqlite_pragmas

ROOT = Path(__file__).resolve().parents[1]
HEAD_REVISION = "20260903_0010"
MANAGED_TABLES = {
    "outfits",
    "images",
    "product_searches",
    "proposal_sets",
    "regeneration_leases",
    "user_operation_leases",
    "worn_views",
    "users",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def _insert(connection, table: str, **values) -> None:
    """Inserta una fila histórica en SQL crudo.

    Estos tests escriben contra el esquema de una revisión concreta, no contra los
    modelos actuales: usar el ORM aquí probaría el esquema de hoy y no el de entonces.
    Las columnas las decide cada llamada porque cambian de una revisión a otra.
    """
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    connection.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values)


@contextmanager
def _database_at(tmp_path, name: str, revision: str, *, foreign_keys: bool = False):
    """Abre una base migrada hasta `revision` y garantiza que el motor se cierra."""
    database_url = f"sqlite:///{tmp_path / name}"
    config = _alembic_config(database_url)
    command.upgrade(config, revision)

    engine = create_engine(database_url)
    if foreign_keys:
        event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        yield engine, config
    finally:
        engine.dispose()


def test_initial_migration_matches_sqlalchemy_models(tmp_path):
    with _database_at(tmp_path, "fresh.db", "head") as (engine, config):
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection)
            assert migration_context.get_current_revision() == HEAD_REVISION
            assert compare_metadata(migration_context, Base.metadata) == []

        assert MANAGED_TABLES <= set(inspect(engine).get_table_names())

        with engine.connect() as connection:
            admin = connection.execute(
                text("SELECT username, password_hash, role, is_active FROM users")
            ).one()
            assert admin.username == "admin"
            assert admin.password_hash != "test"
            assert admin.password_hash.startswith("$argon2id$")
            assert admin.role == "admin"
            assert admin.is_active == 1

        command.downgrade(config, "base")
        assert MANAGED_TABLES.isdisjoint(inspect(engine).get_table_names())


def test_stamp_adopts_existing_schema_without_touching_data(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'existing.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    outfits = Base.metadata.tables["outfits"]

    try:
        with engine.begin() as connection:
            connection.execute(
                outfits.insert().values(
                    user_description="chaqueta negra y vaqueros",
                    outfit_json="{}",
                    image_prompt="prompt",
                    text_model="test-model",
                    created_at=datetime.now(timezone.utc),
                )
            )

        config = _alembic_config(database_url)
        command.stamp(config, "head")
        command.upgrade(config, "head")

        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection)
            assert migration_context.get_current_revision() == HEAD_REVISION
            assert connection.scalar(select(outfits.c.user_description)) == (
                "chaqueta negra y vaqueros"
            )
    finally:
        engine.dispose()


def test_prompt_migration_preserves_legacy_images_without_inventing_prompt(tmp_path):
    with _database_at(tmp_path, "legacy.db", "20260716_0001") as (engine, config):
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            _insert(
                connection,
                "outfits",
                id=1,
                user_description="chaqueta negra y vaqueros",
                outfit_json="{}",
                image_prompt="base prompt",
                text_model="test-model",
                created_at=now,
            )
            # A esta altura `images` todavía no tiene `generation_prompt`.
            _insert(
                connection,
                "images",
                id=1,
                outfit_id=1,
                path="/images/legacy.png",
                image_model="test-image",
                quality="low",
                size="1024x1024",
                cost_estimate=0.006,
                created_at=now,
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("images")}
            assert "generation_prompt" in columns
            assert (
                connection.scalar(text("SELECT generation_prompt FROM images WHERE id = 1")) is None
            )


def test_owner_migration_assigns_existing_outfits_to_admin(tmp_path):
    with _database_at(tmp_path, "before-owners.db", "20260809_0006") as (engine, config):
        with engine.begin() as connection:
            _insert(
                connection,
                "outfits",
                user_description="chaqueta negra y vaqueros",
                outfit_json="{}",
                image_prompt="prompt",
                text_model="test-model",
                created_at=datetime.now(timezone.utc),
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            owner = connection.execute(
                text(
                    """
                    SELECT users.username, users.role
                    FROM outfits
                    JOIN users ON users.id = outfits.owner_id
                    """
                )
            ).one()
            assert owner.username == "admin"
            assert owner.role == "admin"


def test_worn_view_migration_preserves_images_and_enforces_one_view_per_source(tmp_path):
    with _database_at(tmp_path, "before-worn-view.db", "20260717_0002") as (engine, config):
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            _insert(
                connection,
                "outfits",
                id=1,
                user_description="kimono y palazzo",
                outfit_json="{}",
                image_prompt="prompt",
                text_model="test-model",
                created_at=now,
            )
            _insert(
                connection,
                "images",
                id=1,
                outfit_id=1,
                path="/images/source.png",
                generation_prompt="prompt",
                image_model="gpt-image-2",
                quality="low",
                size="1024x1024",
                cost_estimate=0.006,
                created_at=now,
            )

        command.upgrade(config, "head")
        assert "worn_views" in inspect(engine).get_table_names()

        worn_view = {
            "source_image_id": 1,
            "path": "/images/worn.png",
            "generation_prompt": "worn prompt",
            "image_model": "gpt-image-2",
            "quality": "low",
            "size": "1024x1536",
            "cost_estimate": 0.015,
            "created_at": now,
        }
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM images")) == 1
            _insert(connection, "worn_views", **worn_view)

        # La unicidad por imagen fuente hace idempotente la vista puesta.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert(connection, "worn_views", **{**worn_view, "path": "/images/duplicate.png"})


def test_foreign_key_migration_preserves_data_and_cascades_outfit_dependents(tmp_path):
    with _database_at(
        tmp_path,
        "before-foreign-keys.db",
        "20260720_0003",
        foreign_keys=True,
    ) as (engine, config):
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            _insert(
                connection,
                "outfits",
                id=1,
                user_description="kimono y palazzo",
                outfit_json="{}",
                image_prompt="prompt",
                text_model="test-model",
                created_at=now,
            )
            _insert(
                connection,
                "images",
                id=1,
                outfit_id=1,
                path="/images/source.png",
                generation_prompt="prompt",
                image_model="gpt-image-2",
                quality="low",
                size="1024x1024",
                cost_estimate=0.006,
                created_at=now,
            )
            _insert(
                connection,
                "worn_views",
                id=1,
                source_image_id=1,
                path="/images/worn.png",
                generation_prompt="worn prompt",
                image_model="gpt-image-2",
                quality="low",
                size="1024x1536",
                cost_estimate=0.015,
                created_at=now,
            )
            _insert(
                connection,
                "regeneration_leases",
                outfit_id=1,
                token="lease-token",
                acquired_at=now,
            )

        command.upgrade(config, "head")

        image_foreign_keys = inspect(engine).get_foreign_keys("images")
        assert len(image_foreign_keys) == 1
        image_foreign_key = image_foreign_keys[0]
        assert image_foreign_key["name"] == "fk_images_outfit_id_outfits"
        assert image_foreign_key["constrained_columns"] == ["outfit_id"]
        assert image_foreign_key["referred_table"] == "outfits"
        assert image_foreign_key["referred_columns"] == ["id"]
        assert image_foreign_key["options"] == {"ondelete": "CASCADE"}

        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM images")) == 1
            _insert(
                connection,
                "product_searches",
                outfit_id=1,
                item_index=0,
                query="kimono azul comprar online España",
                additional_details=None,
                candidates_json="[]",
                search_model="gpt-5.4-nano",
                web_search_calls=1,
                input_tokens=8000,
                output_tokens=300,
                cost_estimate=0.012,
                created_at=now,
            )
            # Borrar el outfit debe arrastrar a todos sus dependientes.
            connection.execute(text("DELETE FROM outfits WHERE id = 1"))
            assert connection.scalar(text("SELECT COUNT(*) FROM images")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM worn_views")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM regeneration_leases")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM product_searches")) == 0
