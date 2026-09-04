from unittest.mock import Mock, patch

from factories import make_outfit, make_user, shirt_and_trousers

from app.models import Image, Outfit
from app.schemas import OutfitRequest, ProductSearchRequest
from app.services import openai_image, outfit_service
from app.services.image_access_service import get_accessible_generated_image
from app.services.product_search_service import search_persisted_outfit_item


def test_created_outfit_is_assigned_to_the_authenticated_user(db_session):
    owner = make_user(db_session, "owner")
    extraction = shirt_and_trousers()

    with patch.object(
        outfit_service,
        "extract_outfit_from_text",
        return_value=(extraction, "test-model", None),
    ):
        result = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="camisa blanca y pantalón negro",
                generate_image=False,
            ),
            db_session,
            owner,
        )

    assert result.status == "completed"
    assert db_session.get(Outfit, result.outfit_id).owner_id == owner.id


def test_user_lists_and_opens_only_owned_outfits_while_admin_sees_all(db_session):
    first_user = make_user(db_session, "first")
    second_user = make_user(db_session, "second")
    admin = make_user(db_session, "admin-owner-test", role="admin")
    first = make_outfit(db_session, first_user, "camisa blanca y pantalón negro")
    second = make_outfit(db_session, second_user, "jersey rojo y vaqueros azules")

    first_list = outfit_service.list_persisted_outfits(
        db_session,
        current_user=first_user,
    )
    admin_list = outfit_service.list_persisted_outfits(
        db_session,
        current_user=admin,
    )

    assert [item.outfit_id for item in first_list] == [first.id]
    assert {item.outfit_id for item in admin_list} == {first.id, second.id}
    assert outfit_service.get_persisted_outfit(first.id, db_session, first_user) is not None
    assert outfit_service.get_persisted_outfit(second.id, db_session, first_user) is None


def test_foreign_user_is_rejected_before_any_paid_operation_or_delete(db_session):
    owner = make_user(db_session, "owner")
    outsider = make_user(db_session, "outsider")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    image = Image(
        outfit_id=outfit.id,
        path="/images/source.png",
        generation_prompt="reviewed prompt",
        image_model="gpt-image-2",
        quality="low",
        size="1024x1024",
        cost_estimate=0.006,
    )
    db_session.add(image)
    db_session.commit()

    with (
        patch.object(outfit_service, "generate_outfit_image") as image_generation,
        patch.object(outfit_service, "generate_worn_view_image") as worn_generation,
        patch(
            "app.services.product_search_service.search_products",
            new=Mock(),
        ) as product_search,
    ):
        assert outfit_service.regenerate_outfit_image(outfit.id, db_session, outsider) is None
        assert (
            outfit_service.generate_worn_view(
                outfit.id,
                image.id,
                db_session,
                outsider,
            )
            is None
        )
        assert (
            search_persisted_outfit_item(
                outfit.id,
                0,
                ProductSearchRequest(),
                db_session,
                outsider,
            )
            is None
        )
        assert outfit_service.delete_persisted_outfit(outfit.id, db_session, outsider) is False

    image_generation.assert_not_called()
    worn_generation.assert_not_called()
    product_search.assert_not_called()
    assert db_session.get(Outfit, outfit.id) is not None


def test_generated_images_follow_the_same_ownership_rule(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(openai_image, "GENERATED_DIR", tmp_path)
    owner = make_user(db_session, "owner")
    outsider = make_user(db_session, "outsider")
    admin = make_user(db_session, "image-admin", role="admin")
    outfit = make_outfit(db_session, owner, "camisa blanca y pantalón negro")
    image_path = tmp_path / "owned.png"
    image_path.write_bytes(b"png")
    db_session.add(
        Image(
            outfit_id=outfit.id,
            path="/images/owned.png",
            generation_prompt="reviewed prompt",
            image_model="gpt-image-2",
            quality="low",
            size="1024x1024",
            cost_estimate=0.006,
        )
    )
    db_session.commit()

    assert get_accessible_generated_image("/images/owned.png", owner, db_session) == image_path
    assert get_accessible_generated_image("/images/owned.png", outsider, db_session) is None
    assert get_accessible_generated_image("/images/owned.png", admin, db_session) == image_path
    assert get_accessible_generated_image("/images/untracked.png", owner, db_session) is None
