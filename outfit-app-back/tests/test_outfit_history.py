from datetime import datetime, timedelta, timezone

import pytest
from factories import shirt_and_trousers

from app.models import Image, Outfit, ProductSearch, RegenerationLease, WornView
from app.services import openai_image
from app.services.outfit_service import (
    RegenerationInProgressError,
    delete_persisted_outfit,
    get_persisted_outfit,
    list_persisted_outfits,
)


def _persist_outfit(db, *, created_at: datetime, with_images: bool) -> Outfit:
    extraction = shirt_and_trousers()
    outfit = Outfit(
        user_description="camisa blanca y pantalón negro",
        outfit_json=extraction.model_dump_json(),
        image_prompt="reviewed prompt",
        text_model="gpt-5.4-nano",
        created_at=created_at,
    )
    db.add(outfit)
    db.flush()

    if with_images:
        original = Image(
            outfit_id=outfit.id,
            path="/images/original.png",
            generation_prompt="reviewed prompt",
            image_model="gpt-image-2",
            quality="low",
            size="1024x1024",
            cost_estimate=0.006,
            created_at=created_at + timedelta(minutes=1),
        )
        variation = Image(
            outfit_id=outfit.id,
            path="/images/variation.png",
            generation_prompt="reviewed prompt\nAlternative composition",
            image_model="gpt-image-2",
            quality="low",
            size="1024x1024",
            cost_estimate=0.006,
            created_at=created_at + timedelta(minutes=2),
        )
        db.add_all([original, variation])
        db.flush()
        db.add(
            WornView(
                source_image_id=variation.id,
                path="/images/worn.png",
                generation_prompt="worn prompt",
                image_model="gpt-image-2",
                quality="low",
                size="1024x1536",
                cost_estimate=0.014137,
                created_at=created_at + timedelta(minutes=3),
            )
        )

    db.commit()
    return outfit


def test_list_includes_pending_and_generated_outfits(db_session):
    now = datetime.now(timezone.utc)
    generated = _persist_outfit(db_session, created_at=now, with_images=True)
    pending = _persist_outfit(
        db_session,
        created_at=now + timedelta(minutes=10),
        with_images=False,
    )

    result = list_persisted_outfits(db_session)

    assert [item.outfit_id for item in result] == [pending.id, generated.id]
    assert result[0].images == []
    assert result[0].regeneration_count == 0
    assert result[0].regenerations_remaining == 3
    assert result[0].worn_view_preview is None
    assert [state.query for state in result[0].product_search_items] == [
        "camisa blanca comprar online España",
        "pantalón negro comprar online España",
    ]


def test_detail_reconstructs_images_worn_view_and_remaining_limit(db_session):
    outfit = _persist_outfit(
        db_session,
        created_at=datetime.now(timezone.utc),
        with_images=True,
    )

    detail = get_persisted_outfit(outfit.id, db_session)

    assert detail is not None
    assert detail.user_description == outfit.user_description
    assert detail.outfit.items[0].visual_phrase_en == "white shirt"
    assert detail.image_prompt == "reviewed prompt"
    assert [image.generation_number for image in detail.images] == [1, 2]
    assert detail.images[1].worn_view is not None
    assert detail.images[1].worn_view.image.url_or_base64 == "/images/worn.png"
    assert detail.regeneration_count == 1
    assert detail.regenerations_remaining == 2
    assert detail.worn_view_preview is not None
    assert len(detail.product_search_items) == 2
    assert all(state.search is None for state in detail.product_search_items)


def test_detail_returns_none_for_unknown_outfit(db_session):
    assert get_persisted_outfit(9999, db_session) is None


def test_delete_outfit_cascades_rows_and_removes_only_its_managed_files(
    db_session,
    monkeypatch,
    tmp_path,
):
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    monkeypatch.setattr(openai_image, "GENERATED_DIR", generated_dir)
    outfit = _persist_outfit(
        db_session,
        created_at=datetime.now(timezone.utc),
        with_images=True,
    )
    outfit_id = outfit.id
    referenced = {"original.png", "variation.png", "worn.png"}
    for filename in referenced | {"unrelated.png"}:
        (generated_dir / filename).write_bytes(b"png")
    db_session.add(
        ProductSearch(
            outfit_id=outfit_id,
            item_index=0,
            query="camisa blanca comprar online España",
            candidates_json="[]",
            search_model="gpt-5.4-nano",
            web_search_calls=1,
            input_tokens=8000,
            output_tokens=300,
            cost_estimate=0.012,
        )
    )
    db_session.commit()

    deleted = delete_persisted_outfit(outfit_id, db_session)

    assert deleted is True
    assert db_session.get(Outfit, outfit_id) is None
    assert db_session.query(Image).count() == 0
    assert db_session.query(WornView).count() == 0
    assert db_session.query(ProductSearch).count() == 0
    assert db_session.query(RegenerationLease).count() == 0
    assert all(not (generated_dir / filename).exists() for filename in referenced)
    assert (generated_dir / "unrelated.png").is_file()


def test_delete_outfit_returns_false_without_touching_files_when_missing(
    db_session,
    monkeypatch,
    tmp_path,
):
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    unrelated = generated_dir / "unrelated.png"
    unrelated.write_bytes(b"png")
    monkeypatch.setattr(openai_image, "GENERATED_DIR", generated_dir)

    assert delete_persisted_outfit(9999, db_session) is False
    assert unrelated.is_file()


def test_delete_outfit_is_blocked_while_generation_lease_is_active(
    db_session,
    monkeypatch,
    tmp_path,
):
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    monkeypatch.setattr(openai_image, "GENERATED_DIR", generated_dir)
    outfit = _persist_outfit(
        db_session,
        created_at=datetime.now(timezone.utc),
        with_images=True,
    )
    original = generated_dir / "original.png"
    original.write_bytes(b"png")
    db_session.add(
        RegenerationLease(
            outfit_id=outfit.id,
            token="active-generation",
            acquired_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    with pytest.raises(RegenerationInProgressError):
        delete_persisted_outfit(outfit.id, db_session)

    assert db_session.get(Outfit, outfit.id) is not None
    assert db_session.query(Image).count() == 2
    assert db_session.query(WornView).count() == 1
    assert db_session.query(RegenerationLease).count() == 1
    assert original.is_file()
