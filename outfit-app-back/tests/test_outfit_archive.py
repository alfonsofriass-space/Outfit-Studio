import pytest
from factories import make_image, make_outfit, make_user
from sqlalchemy import text

from app.models import Image, Outfit
from app.schemas import OutfitUpdateRequest
from app.services.outfit_service import (
    OutfitImageNotInOutfitError,
    delete_persisted_outfit,
    list_persisted_outfits,
    update_persisted_outfit,
)

# ------------------------------------------------------------------ estado inicial


def test_an_outfit_starts_without_a_chosen_cover_and_unmarked(db_session):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    make_image(db_session, outfit, "primera")

    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(is_favourite=False), db_session, owner
    )

    assert detail.chosen_image_id is None
    assert detail.is_favourite is False


# --------------------------------------------------------------------- portada


def test_choosing_a_composition_persists_it_as_the_cover(db_session):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    make_image(db_session, outfit, "original")
    variacion = make_image(db_session, outfit, "variacion")

    detail = update_persisted_outfit(
        outfit.id,
        OutfitUpdateRequest(chosen_image_id=variacion.id),
        db_session,
        owner,
    )

    assert detail.chosen_image_id == variacion.id


def test_moving_the_cover_never_leaves_two_chosen_at_once(db_session):
    """El índice único parcial rechazaría dos elegidas en el mismo outfit."""
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    primera = make_image(db_session, outfit, "original")
    segunda = make_image(db_session, outfit, "variacion")

    update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=primera.id), db_session, owner
    )
    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=segunda.id), db_session, owner
    )

    assert detail.chosen_image_id == segunda.id
    elegidas = db_session.scalars(
        text("select count(*) from images where outfit_id = :oid and is_chosen = 1").bindparams(
            oid=outfit.id
        )
    ).all()
    assert elegidas == [1]


def test_clearing_the_cover_is_possible_with_an_explicit_null(db_session):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    imagen = make_image(db_session, outfit, "original")
    update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=imagen.id), db_session, owner
    )

    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=None), db_session, owner
    )

    assert detail.chosen_image_id is None


def test_a_composition_from_another_outfit_is_rejected(db_session):
    owner = make_user(db_session, "alfonso")
    mio = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    otro = make_outfit(db_session, owner, "abrigo largo de lana")
    ajena = make_image(db_session, otro, "ajena")

    with pytest.raises(OutfitImageNotInOutfitError):
        update_persisted_outfit(
            mio.id, OutfitUpdateRequest(chosen_image_id=ajena.id), db_session, owner
        )

    assert db_session.get(Image, ajena.id).is_chosen is False


# -------------------------------------------------------------------- favoritos


def test_marking_a_favourite_does_not_touch_the_cover(db_session):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    imagen = make_image(db_session, outfit, "original")
    update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=imagen.id), db_session, owner
    )

    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(is_favourite=True), db_session, owner
    )

    assert detail.is_favourite is True
    assert detail.chosen_image_id == imagen.id


# ------------------------------------------------------------------- integridad


def test_deleting_the_chosen_composition_leaves_no_dangling_reference(db_session):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    make_image(db_session, outfit, "original")
    elegida = make_image(db_session, outfit, "elegida")
    update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=elegida.id), db_session, owner
    )

    db_session.delete(db_session.get(Image, elegida.id))
    db_session.commit()

    assert list(db_session.execute(text("pragma foreign_key_check"))) == []
    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(is_favourite=False), db_session, owner
    )
    assert detail.chosen_image_id is None


def test_deleting_an_outfit_with_a_chosen_cover_keeps_integrity(db_session, monkeypatch):
    owner = make_user(db_session, "alfonso")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    elegida = make_image(db_session, outfit, "elegida")
    update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(chosen_image_id=elegida.id), db_session, owner
    )

    assert delete_persisted_outfit(outfit.id, db_session, owner) is True

    assert db_session.get(Outfit, outfit.id) is None
    assert db_session.get(Image, elegida.id) is None
    assert list(db_session.execute(text("pragma foreign_key_check"))) == []


# -------------------------------------------------------------------- propiedad


def test_a_foreign_user_cannot_change_the_archive(db_session):
    owner = make_user(db_session, "alfonso")
    intruso = make_user(db_session, "otra")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")

    assert (
        update_persisted_outfit(
            outfit.id, OutfitUpdateRequest(is_favourite=True), db_session, intruso
        )
        is None
    )
    assert db_session.get(Outfit, outfit.id).is_favourite is False


def test_the_admin_can_change_a_foreign_archive(db_session):
    owner = make_user(db_session, "alfonso")
    admin = make_user(db_session, "admin", role="admin")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")

    detail = update_persisted_outfit(
        outfit.id, OutfitUpdateRequest(is_favourite=True), db_session, admin
    )

    assert detail.is_favourite is True


# ------------------------------------------------------------------ paginación


def test_offset_pages_without_gaps_or_repeats(db_session):
    owner = make_user(db_session, "alfonso")
    # Mismo `created_at` a propósito: el desempate por id es lo que evita huecos.
    creados = [make_outfit(db_session, owner, f"outfit {index}") for index in range(5)]

    primera = list_persisted_outfits(db_session, limit=2, current_user=owner)
    segunda = list_persisted_outfits(db_session, limit=2, current_user=owner, offset=2)
    tercera = list_persisted_outfits(db_session, limit=2, current_user=owner, offset=4)

    vistos = [item.outfit_id for item in primera + segunda + tercera]
    assert len(vistos) == len(creados)
    assert len(set(vistos)) == len(creados)


def test_pagination_respects_the_owner_filter(db_session):
    owner = make_user(db_session, "alfonso")
    otra = make_user(db_session, "otra")
    make_outfit(db_session, owner, "mío uno")
    make_outfit(db_session, otra, "ajeno")
    make_outfit(db_session, owner, "mío dos")

    propios = list_persisted_outfits(db_session, limit=10, current_user=owner)
    ajenos = list_persisted_outfits(db_session, limit=10, current_user=otra)

    assert len(propios) == 2
    assert len(ajenos) == 1
    assert {item.user_description for item in propios} == {"mío uno", "mío dos"}


def test_the_favourites_filter_runs_in_the_query_not_in_the_client(db_session):
    """Filtrar después de paginar solo miraría la página cargada."""
    owner = make_user(db_session, "alfonso")
    for index in range(3):
        outfit = make_outfit(db_session, owner, f"outfit {index}")
        if index == 2:
            update_persisted_outfit(
                outfit.id, OutfitUpdateRequest(is_favourite=True), db_session, owner
            )

    todos = list_persisted_outfits(db_session, limit=2, current_user=owner)
    favoritos = list_persisted_outfits(
        db_session, limit=2, current_user=owner, favourites_only=True
    )

    assert len(todos) == 2
    assert len(favoritos) == 1
    assert favoritos[0].user_description == "outfit 2"
    assert favoritos[0].is_favourite is True
