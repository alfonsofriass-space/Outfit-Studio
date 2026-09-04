from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import Image, Outfit, RegenerationLease, WornView
from app.schemas import ImageDetails, OutfitExtraction, OutfitItem
from app.services import openai_image, outfit_service
from app.services.openai_image import ImageGenerationError, WornImageGeneration


def _extraction() -> OutfitExtraction:
    return OutfitExtraction(
        status="ok",
        outfit_summary="kimono con palazzo y accesorios",
        items=[
            OutfitItem(
                category="upper",
                item_type="kimono",
                certainty="high",
                visual_phrase_en="blue printed kimono",
            ),
            OutfitItem(
                category="upper",
                item_type="top",
                certainty="high",
                visual_phrase_en="white top",
            ),
            OutfitItem(
                category="lower",
                item_type="pantalón palazzo",
                certainty="high",
                visual_phrase_en="beige palazzo pants",
            ),
            OutfitItem(
                category="accessory",
                item_type="bolso",
                certainty="high",
                visual_phrase_en="raffia bag",
            ),
        ],
        styling_notes_en=["white top tucked into beige palazzo pants"],
    )


def _create_source(db, tmp_path, monkeypatch, *, outfit_id=None):
    monkeypatch.setattr(openai_image, "GENERATED_DIR", tmp_path)
    source_path = tmp_path / "source.png"
    source_path.write_bytes(b"flat-lay")
    outfit = Outfit(
        id=outfit_id,
        user_description="kimono azul, top blanco, palazzo beige y bolso de rafia",
        outfit_json=_extraction().model_dump_json(),
        image_prompt="flat-lay prompt",
        text_model="test-model",
    )
    db.add(outfit)
    db.flush()
    image = Image(
        outfit_id=outfit.id,
        path="/images/source.png",
        generation_prompt="flat-lay prompt",
        image_model="gpt-image-2",
        quality="low",
        size="1024x1024",
        cost_estimate=0.006,
    )
    db.add(image)
    db.commit()
    return outfit, image, source_path


def _generated_worn() -> WornImageGeneration:
    return WornImageGeneration(
        image=ImageDetails(
            model="gpt-image-2",
            quality="low",
            size="1024x1536",
            url_or_base64="/images/worn.png",
        ),
        cost_estimate=0.014252,
    )


def test_worn_prompt_promotes_the_validated_reference_contract():
    prompt = outfit_service.build_worn_view_prompt(_extraction())

    assert "supplied flat-lay board as the visual source of truth" in prompt
    assert "neutral adult fashion mannequin" in prompt
    assert "blue printed kimono; white top" in prompt
    assert "white top tucked into beige palazzo pants" in prompt
    assert "natural overlap required when clothes are worn" in prompt


def test_worn_view_is_persisted_once_and_reused_without_second_call(
    db_session,
    tmp_path,
    monkeypatch,
):
    outfit, image, _ = _create_source(db_session, tmp_path, monkeypatch)

    with patch.object(
        outfit_service,
        "generate_worn_view_image",
        return_value=_generated_worn(),
    ) as image_mock:
        first = outfit_service.generate_worn_view(outfit.id, image.id, db_session)
        second = outfit_service.generate_worn_view(outfit.id, image.id, db_session)

    assert first.created is True
    assert second.created is False
    assert second.worn_view.worn_view_id == first.worn_view.worn_view_id
    assert first.worn_view.cost_estimate == pytest.approx(0.014252)
    assert first.worn_view.image.size == "1024x1536"
    image_mock.assert_called_once()
    assert db_session.query(WornView).count() == 1
    assert db_session.query(RegenerationLease).count() == 0


def test_missing_flat_lay_fails_before_provider_or_lease(
    db_session,
    tmp_path,
    monkeypatch,
):
    outfit, image, source_path = _create_source(db_session, tmp_path, monkeypatch)
    source_path.unlink()

    with patch.object(outfit_service, "generate_worn_view_image") as image_mock:
        with pytest.raises(outfit_service.WornViewUnavailableError, match="composición local"):
            outfit_service.generate_worn_view(outfit.id, image.id, db_session)

    image_mock.assert_not_called()
    assert db_session.query(WornView).count() == 0
    assert db_session.query(RegenerationLease).count() == 0


def test_active_outfit_lease_blocks_worn_view_before_provider(
    db_session,
    tmp_path,
    monkeypatch,
):
    outfit, image, _ = _create_source(db_session, tmp_path, monkeypatch)
    db_session.add(
        RegenerationLease(
            outfit_id=outfit.id,
            token="other-request",
            acquired_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    with patch.object(outfit_service, "generate_worn_view_image") as image_mock:
        with pytest.raises(outfit_service.RegenerationInProgressError):
            outfit_service.generate_worn_view(outfit.id, image.id, db_session)

    image_mock.assert_not_called()
    assert db_session.query(WornView).count() == 0
    assert db_session.query(RegenerationLease).count() == 1


def test_provider_failure_persists_nothing_and_releases_lease(
    db_session,
    tmp_path,
    monkeypatch,
):
    outfit, image, _ = _create_source(db_session, tmp_path, monkeypatch)

    with patch.object(
        outfit_service,
        "generate_worn_view_image",
        side_effect=ImageGenerationError("timeout"),
    ):
        with pytest.raises(ImageGenerationError):
            outfit_service.generate_worn_view(outfit.id, image.id, db_session)

    assert db_session.query(WornView).count() == 0
    assert db_session.query(RegenerationLease).count() == 0
    assert db_session.query(Image).count() == 1


def test_worn_view_stays_nested_and_does_not_consume_a_regeneration(
    db_session,
    tmp_path,
    monkeypatch,
):
    outfit, image, _ = _create_source(db_session, tmp_path, monkeypatch)
    with patch.object(
        outfit_service,
        "generate_worn_view_image",
        return_value=_generated_worn(),
    ):
        outfit_service.generate_worn_view(outfit.id, image.id, db_session)

    detail = outfit_service.get_persisted_outfit(outfit.id, db_session)
    assert detail is not None
    assert len(detail.images) == 1
    assert detail.images[0].generation_number == 1
    assert detail.images[0].worn_view is not None
    assert detail.images[0].worn_view.source_image_id == image.id

    with patch.object(
        outfit_service,
        "generate_outfit_image",
        return_value=ImageDetails(
            model="gpt-image-2",
            quality="low",
            size="1024x1024",
            url_or_base64="/images/variation.png",
        ),
    ):
        variation = outfit_service.regenerate_outfit_image(outfit.id, db_session)

    assert variation.regeneration_count == 1
    assert variation.regenerations_remaining == 2
    assert variation.image_id != image.id
    assert variation.worn_view_preview is not None
    assert db_session.query(Image).count() == 2
    assert db_session.query(WornView).count() == 1


def test_worn_view_rejects_image_from_another_outfit_without_provider(
    db_session,
    tmp_path,
    monkeypatch,
):
    first, _, _ = _create_source(db_session, tmp_path, monkeypatch, outfit_id=1)
    _, other_image, _ = _create_source(db_session, tmp_path, monkeypatch, outfit_id=2)

    with patch.object(outfit_service, "generate_worn_view_image") as image_mock:
        result = outfit_service.generate_worn_view(first.id, other_image.id, db_session)

    assert result is None
    image_mock.assert_not_called()
