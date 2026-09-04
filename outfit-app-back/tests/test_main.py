import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.config import ConfigurationError, Settings
from app.main import app
from app.models import User
from app.schemas import (
    ClarificationResponse,
    ImageDetails,
    OutfitDetailResponse,
    ProductSearchDetails,
    ProductSearchResponse,
    WornViewDetails,
    WornViewResponse,
)
from app.services.openai_image import GENERATED_DIR, ImageGenerationError
from app.services.openai_product_search import (
    ProductSearchIncompleteError,
    ProductSearchProviderError,
    ProductSearchServiceUnavailableError,
)
from app.services.openai_proposals import (
    ProposalModelOutputError,
    ProposalServiceUnavailableError,
)
from app.services.openai_text import (
    TextModelOutputError,
    TextServiceUnavailableError,
)
from app.services.outfit_operation_lease import OutfitOperationInProgressError
from app.services.outfit_service import (
    OutfitImageNotInOutfitError,
    OutfitImagePromptMissingError,
    RegenerationInProgressError,
    WornViewUnavailableError,
)
from app.services.product_search_service import ProductSearchInputError
from app.services.user_operation_lease import UserOperationInProgressError

client = TestClient(app)

_TEST_ADMIN = User(
    id=999,
    username="test-admin",
    password_hash="unused-in-endpoint-tests",
    role="admin",
    is_active=True,
    created_at=datetime.now(timezone.utc),
)


@pytest.fixture(autouse=True)
def authenticated_admin():
    app.dependency_overrides[get_current_user] = lambda: _TEST_ADMIN
    yield
    app.dependency_overrides.pop(get_current_user, None)


# PNG 1x1 válido para servir en los tests de /images
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_OUTFIT_CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "outfit-detail.v1.json"


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_rejects_missing_openai_api_key():
    settings = Settings(_env_file=None, openai_api_key=None)

    with patch("app.main.get_settings", return_value=settings):
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            with TestClient(app):
                pass


def test_generated_images_are_served():
    """
    Las rutas /images/<file> que la API devuelve en url_or_base64 deben ser
    descargables por un cliente remoto (fix del hallazgo F2: antes no había
    ninguna ruta que sirviera app/generated/).
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    name = f"test_{uuid.uuid4().hex}.png"
    filepath = GENERATED_DIR / name
    filepath.write_bytes(_TINY_PNG)
    try:
        with patch(
            "app.main.get_accessible_generated_image",
            return_value=filepath,
        ) as image_access:
            response = client.get(f"/images/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == _TINY_PNG
        assert image_access.call_args.args[1] is _TEST_ADMIN
    finally:
        filepath.unlink()


def test_missing_image_returns_404():
    with patch("app.main.get_accessible_generated_image", return_value=None):
        response = client.get(f"/images/{uuid.uuid4().hex}.png")
    assert response.status_code == 404


def test_too_long_description_rejected_before_any_processing():
    """
    Fix F7: una descripción de longitud arbitraria se rechaza con 422 en la capa
    de validación — nunca llega a la heurística ni a la API de OpenAI.
    """
    response = client.post(
        "/outfits/generate",
        json={"user_description": "chaqueta negra y vaqueros " + "x" * 500},
    )
    assert response.status_code == 422


def test_empty_description_rejected():
    response = client.post("/outfits/generate", json={"user_description": ""})
    assert response.status_code == 422


def test_text_service_unavailable_returns_503():
    with patch(
        "app.main.process_outfit_request",
        side_effect=TextServiceUnavailableError("upstream unavailable"),
    ):
        response = client.post(
            "/outfits/generate",
            json={"user_description": "chaqueta negra y vaqueros azules"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "El servicio de análisis de outfits no está disponible temporalmente."
    }


def test_unusable_text_response_returns_502():
    with patch(
        "app.main.process_outfit_request",
        side_effect=TextModelOutputError("invalid structured output"),
    ):
        response = client.post(
            "/outfits/generate",
            json={"user_description": "chaqueta negra y vaqueros azules"},
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "No se pudo procesar la respuesta del servicio de análisis."
    }


def test_concurrent_regeneration_returns_409():
    with patch(
        "app.main.regenerate_outfit_image",
        side_effect=RegenerationInProgressError("already running"),
    ):
        response = client.post("/outfits/1/regenerate")

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Ya hay una generación en curso para este outfit. "
            "Espera a que termine antes de volver a intentarlo."
        )
    }


def test_regeneration_without_visual_prompt_returns_409():
    with patch(
        "app.main.regenerate_outfit_image",
        side_effect=OutfitImagePromptMissingError("missing prompt"),
    ):
        response = client.post("/outfits/1/regenerate")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Este outfit no tiene un prompt visual válido y no puede generar una imagen."
    }


def test_outfit_list_uses_the_shared_persistent_contract():
    payload = json.loads(_OUTFIT_CONTRACT.read_text(encoding="utf-8"))
    item = OutfitDetailResponse.model_validate(payload)

    with patch("app.main.list_persisted_outfits", return_value=[item]) as list_mock:
        response = client.get("/outfits?limit=8")

    assert response.status_code == 200
    list_mock.assert_called_once()
    assert list_mock.call_args.kwargs["limit"] == 8
    assert response.json() == [payload]


def test_outfit_detail_uses_the_shared_persistent_contract():
    payload = json.loads(_OUTFIT_CONTRACT.read_text(encoding="utf-8"))
    item = OutfitDetailResponse.model_validate(payload)

    with patch("app.main.get_persisted_outfit", return_value=item) as detail_mock:
        response = client.get("/outfits/17")

    assert response.status_code == 200
    detail_mock.assert_called_once()
    assert detail_mock.call_args.args[0] == 17
    assert response.json() == payload


def test_outfit_detail_returns_404_when_missing():
    with patch("app.main.get_persisted_outfit", return_value=None):
        response = client.get("/outfits/9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Outfit no encontrado"}


def test_product_search_endpoint_returns_one_persisted_result():
    result = ProductSearchResponse(
        created=True,
        outfit_id=17,
        item_index=1,
        search=ProductSearchDetails(
            item_index=1,
            query="pantalón negro comprar online España",
            candidates=[],
            model="gpt-5.4-nano",
            web_search_calls=1,
            input_tokens=8700,
            output_tokens=420,
            cost_estimate=0.012265,
            created_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        ),
    )

    with patch(
        "app.main.search_persisted_outfit_item",
        return_value=result,
    ) as search_mock:
        response = client.post(
            "/outfits/17/items/1/product-search",
            json={"additional_details": "  Versace   verde militar  "},
        )

    assert response.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["search"]["candidates"] == []
    assert search_mock.call_args.args[:2] == (17, 1)
    assert search_mock.call_args.args[2].additional_details == "Versace verde militar"


@pytest.mark.parametrize(
    ("side_effect", "expected_status", "expected_detail"),
    [
        (
            ProductSearchInputError("Añade color o material."),
            422,
            "Añade color o material.",
        ),
        (
            OutfitOperationInProgressError("active"),
            409,
            "Ya hay otra operación de pago en curso para este outfit. "
            "Espera a que termine antes de buscar otra prenda.",
        ),
        (
            ProductSearchServiceUnavailableError("unavailable"),
            503,
            "El servicio de búsqueda de productos no está disponible temporalmente. "
            "No se ha realizado un reintento automático.",
        ),
        (
            ProductSearchIncompleteError("incomplete"),
            502,
            "La búsqueda terminó antes de devolver resultados completos. "
            "No se ha realizado un reintento automático.",
        ),
        (
            ProductSearchProviderError("provider failed"),
            502,
            "La búsqueda no devolvió un resultado utilizable. "
            "No se ha realizado un reintento automático.",
        ),
    ],
)
def test_product_search_endpoint_maps_safe_failures(
    side_effect,
    expected_status,
    expected_detail,
):
    with patch(
        "app.main.search_persisted_outfit_item",
        side_effect=side_effect,
    ):
        response = client.post(
            "/outfits/17/items/1/product-search",
            json={"additional_details": None},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_product_search_endpoint_returns_404_for_unknown_item():
    with patch("app.main.search_persisted_outfit_item", return_value=None):
        response = client.post(
            "/outfits/17/items/99/product-search",
            json={"additional_details": None},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Outfit o prenda no encontrados"}


def test_delete_outfit_returns_204():
    with patch("app.main.delete_persisted_outfit", return_value=True) as delete_mock:
        response = client.delete("/outfits/17")

    assert response.status_code == 204
    assert response.content == b""
    delete_mock.assert_called_once()
    assert delete_mock.call_args.args[0] == 17


def test_delete_outfit_returns_404_when_missing():
    with patch("app.main.delete_persisted_outfit", return_value=False):
        response = client.delete("/outfits/9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Outfit no encontrado"}


def test_delete_outfit_returns_409_during_image_generation():
    with patch(
        "app.main.delete_persisted_outfit",
        side_effect=RegenerationInProgressError("active"),
    ):
        response = client.delete("/outfits/17")

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "No se puede eliminar este outfit mientras tiene una generación de imagen en curso."
        )
    }


def test_worn_view_endpoint_returns_persisted_pair():
    created_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    result = WornViewResponse(
        created=True,
        outfit_id=4,
        source_image_id=9,
        worn_view=WornViewDetails(
            worn_view_id=3,
            source_image_id=9,
            generation_prompt="exact worn prompt",
            image=ImageDetails(
                model="gpt-image-2",
                quality="low",
                size="1024x1536",
                url_or_base64="/images/worn.png",
            ),
            cost_estimate=0.014252,
            created_at=created_at,
        ),
    )

    with patch("app.main.generate_worn_view", return_value=result) as worn_mock:
        response = client.post("/outfits/4/images/9/worn-view")

    assert response.status_code == 200
    worn_mock.assert_called_once()
    assert worn_mock.call_args.args[:2] == (4, 9)
    assert response.json()["created"] is True
    assert response.json()["worn_view"]["image"]["size"] == "1024x1536"


@pytest.mark.parametrize(
    ("side_effect", "expected_status"),
    [
        (WornViewUnavailableError("missing source"), 409),
        (ImageGenerationError("provider failed"), 502),
    ],
)
def test_worn_view_endpoint_maps_safe_failures(side_effect, expected_status):
    with patch("app.main.generate_worn_view", side_effect=side_effect):
        response = client.post("/outfits/4/images/9/worn-view")

    assert response.status_code == expected_status


def test_worn_view_endpoint_returns_404_for_mismatched_source():
    with patch("app.main.generate_worn_view", return_value=None):
        response = client.post("/outfits/4/images/99/worn-view")

    assert response.status_code == 404


# --------------------------------------------------- vía de inspiración (P10A)

_SITUATION = {"situation": "Boda de tarde en octubre, en el campo"}


def test_proposals_route_is_not_read_as_an_outfit_id():
    """`/outfits/proposals` se declara antes que `/outfits/{outfit_id}`."""
    clarification = ClarificationResponse(message="sin situación", suggestion="prueba así")
    with patch("app.main.propose_outfits", return_value=clarification) as service:
        response = client.post("/outfits/proposals", json=_SITUATION)

    service.assert_called_once()
    assert response.status_code == 200


def test_a_second_proposal_request_returns_409():
    with patch(
        "app.main.propose_outfits",
        side_effect=UserOperationInProgressError("lease taken"),
    ):
        response = client.post("/outfits/proposals", json=_SITUATION)

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Ya hay una petición de propuestas en curso. "
            "Espera a que termine antes de volver a intentarlo."
        )
    }


def test_unavailable_proposal_service_returns_503():
    with patch(
        "app.main.propose_outfits",
        side_effect=ProposalServiceUnavailableError("down"),
    ):
        response = client.post("/outfits/proposals", json=_SITUATION)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "El servicio de propuestas no está disponible temporalmente."
    }


def test_unusable_proposals_return_502():
    with patch(
        "app.main.propose_outfits",
        side_effect=ProposalModelOutputError("unusable output"),
    ):
        response = client.post("/outfits/proposals", json=_SITUATION)

    assert response.status_code == 502
    assert response.json() == {"detail": "No se pudo obtener un conjunto de propuestas utilizable."}


def test_missing_proposal_set_returns_404():
    with patch("app.main.get_proposal_set", return_value=None):
        response = client.get("/outfits/proposals/7")

    assert response.status_code == 404


def test_choosing_a_missing_proposal_returns_404():
    with patch("app.main.choose_proposal", return_value=None):
        response = client.post(
            "/outfits/proposals/7/choose",
            json={"proposal_index": 0},
        )

    assert response.status_code == 404


def test_a_negative_proposal_index_is_rejected_by_the_contract():
    response = client.post("/outfits/proposals/7/choose", json={"proposal_index": -1})

    assert response.status_code == 422


# ------------------------------------------------------ archivo del outfit (P11A)


def test_outfit_list_passes_the_requested_page_to_the_service():
    payload = json.loads(_OUTFIT_CONTRACT.read_text(encoding="utf-8"))
    item = OutfitDetailResponse.model_validate(payload)

    with patch("app.main.list_persisted_outfits", return_value=[item]) as list_mock:
        response = client.get("/outfits?limit=8&offset=16")

    assert response.status_code == 200
    assert list_mock.call_args.kwargs["limit"] == 8
    assert list_mock.call_args.kwargs["offset"] == 16


def test_a_negative_offset_is_rejected_by_the_contract():
    response = client.get("/outfits?offset=-1")

    assert response.status_code == 422


def test_updating_the_archive_returns_the_shared_contract():
    payload = json.loads(_OUTFIT_CONTRACT.read_text(encoding="utf-8"))
    item = OutfitDetailResponse.model_validate(payload)

    with patch("app.main.update_persisted_outfit", return_value=item) as update_mock:
        response = client.patch("/outfits/17", json={"is_favourite": True})

    assert response.status_code == 200
    assert response.json() == payload
    assert update_mock.call_args.args[0] == 17


def test_an_update_without_any_field_is_rejected():
    with patch("app.main.update_persisted_outfit") as update_mock:
        response = client.patch("/outfits/17", json={})

    assert response.status_code == 422
    update_mock.assert_not_called()


def test_an_unknown_field_in_the_update_is_rejected():
    with patch("app.main.update_persisted_outfit") as update_mock:
        response = client.patch("/outfits/17", json={"cover": 3})

    assert response.status_code == 422
    update_mock.assert_not_called()


def test_choosing_a_foreign_composition_returns_409():
    with patch(
        "app.main.update_persisted_outfit",
        side_effect=OutfitImageNotInOutfitError("no pertenece"),
    ):
        response = client.patch("/outfits/17", json={"chosen_image_id": 99})

    assert response.status_code == 409
    assert response.json() == {"detail": "Esa composición no pertenece a este outfit."}


def test_updating_a_missing_outfit_returns_404():
    with patch("app.main.update_persisted_outfit", return_value=None):
        response = client.patch("/outfits/17", json={"is_favourite": True})

    assert response.status_code == 404
