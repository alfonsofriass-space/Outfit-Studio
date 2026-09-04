import threading
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Outfit, ProductSearch, RegenerationLease
from app.schemas import (
    OutfitExtraction,
    OutfitItem,
    ProductCandidate,
    ProductSearchRequest,
)
from app.services.openai_product_search import (
    ProductSearchProviderError,
    ProductSearchProviderResult,
)
from app.services.outfit_operation_lease import OutfitOperationInProgressError
from app.services.outfit_service import get_persisted_outfit
from app.services.product_search_service import (
    MAX_PRODUCT_SEARCH_ATTEMPTS,
    ProductSearchInputError,
    ProductSearchLimitError,
    build_product_query,
    search_persisted_outfit_item,
)


def _extraction() -> OutfitExtraction:
    return OutfitExtraction(
        status="ok",
        outfit_summary="Camisa blanca, pantalón negro y bolso",
        items=[
            OutfitItem(
                category="upper",
                item_type="camisa",
                color="blanca",
                material="lino",
                certainty="high",
                visual_phrase_en="white linen shirt",
            ),
            OutfitItem(
                category="lower",
                item_type="pantalón",
                certainty="high",
                visual_phrase_en="trousers",
            ),
            OutfitItem(
                category="accessory",
                item_type="complemento",
                color="verde",
                certainty="medium",
                visual_phrase_en="green accessory",
            ),
        ],
    )


def _persist_outfit(db) -> Outfit:
    outfit = Outfit(
        user_description="camisa blanca de lino, pantalón y complemento verde",
        outfit_json=_extraction().model_dump_json(),
        image_prompt="prompt",
        text_model="gpt-5.4-nano",
        created_at=datetime.now(timezone.utc),
    )
    db.add(outfit)
    db.commit()
    return outfit


def _provider_result(candidates=None) -> ProductSearchProviderResult:
    return ProductSearchProviderResult(
        candidates=candidates or [],
        model="gpt-5.4-nano",
        web_search_calls=1,
        input_tokens=8700,
        output_tokens=420,
        cost_estimate=0.012265,
    )


def test_query_uses_only_persisted_or_user_written_details():
    extraction = _extraction()

    assert build_product_query(extraction.items[0]) == ("camisa blanca lino comprar online España")
    assert build_product_query(extraction.items[0], "cuello mao") == (
        "camisa blanca lino cuello mao comprar online España"
    )
    with pytest.raises(ProductSearchInputError, match="necesita color"):
        build_product_query(extraction.items[1])
    with pytest.raises(ProductSearchInputError, match="demasiado genérico"):
        build_product_query(extraction.items[2])
    assert build_product_query(extraction.items[2], "bolso pequeño") == (
        "bolso pequeño verde comprar online España"
    )


def test_query_keeps_an_explicit_brand_before_visual_attributes():
    item = OutfitItem(
        category="accessory",
        item_type="gorra",
        brand="Versace",
        color="verde militar",
        certainty="high",
        visual_phrase_en="military green Versace cap",
    )

    assert build_product_query(item) == ("gorra Versace verde militar comprar online España")
    assert build_product_query(item, brand="  Versace Jeans Couture  ") == (
        "gorra Versace Jeans Couture verde militar comprar online España"
    )


def test_product_search_request_normalizes_the_single_details_field():
    request = ProductSearchRequest(
        additional_details="  Versace,   verde   militar ",
    )

    assert request.additional_details == "Versace, verde militar"


def test_completed_search_is_persisted_and_second_request_is_free(
    db_session,
    monkeypatch,
):
    outfit = _persist_outfit(db_session)
    provider = Mock(
        return_value=_provider_result(
            [
                ProductCandidate(
                    title="Camisa de lino",
                    store="Mango",
                    product_url="https://shop.mango.com/es/es/p/camisa_123",
                    price_text="39,99 €",
                )
            ]
        )
    )
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    created = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(),
        db_session,
    )
    cached = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(additional_details="este texto no debe repetir la llamada"),
        db_session,
    )

    assert created is not None
    assert created.created is True
    assert cached is not None
    assert cached.created is False
    assert cached.search == created.search
    provider.assert_called_once_with(
        "camisa blanca lino comprar online España",
        brand=None,
    )
    assert db_session.query(ProductSearch).count() == 1
    assert db_session.query(RegenerationLease).count() == 0
    detail = get_persisted_outfit(outfit.id, db_session)
    assert detail is not None
    assert detail.product_search_items[0].search == created.search


def test_force_new_creates_another_attempt_and_keeps_the_previous_one(
    db_session,
    monkeypatch,
):
    """Repetir una búsqueda ya no es un callejón sin salida, pero exige `force_new`."""
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result([]))
    monkeypatch.setattr("app.services.product_search_service.search_products", provider)

    first = search_persisted_outfit_item(outfit.id, 0, ProductSearchRequest(), db_session)
    retried = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(additional_details="Zara", force_new=True),
        db_session,
    )

    assert first is not None and first.search.attempt == 1
    assert retried is not None
    assert retried.created is True
    assert retried.search.attempt == 2
    assert "Zara" in retried.search.query
    assert provider.call_count == 2
    # El intento anterior se conserva: cada uno fue una llamada pagada.
    assert db_session.query(ProductSearch).count() == 2
    assert db_session.query(RegenerationLease).count() == 0

    detail = get_persisted_outfit(outfit.id, db_session)
    assert detail is not None
    state = detail.product_search_items[0]
    assert state.search.attempt == 2  # el panel muestra el más reciente
    assert state.attempts == 2
    assert state.attempts_remaining == 1


def test_attempts_are_capped_before_calling_the_provider(db_session, monkeypatch):
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result([]))
    monkeypatch.setattr("app.services.product_search_service.search_products", provider)

    for _ in range(MAX_PRODUCT_SEARCH_ATTEMPTS):
        search_persisted_outfit_item(
            outfit.id,
            0,
            ProductSearchRequest(force_new=True),
            db_session,
        )
    assert provider.call_count == MAX_PRODUCT_SEARCH_ATTEMPTS

    with pytest.raises(ProductSearchLimitError):
        search_persisted_outfit_item(
            outfit.id,
            0,
            ProductSearchRequest(force_new=True),
            db_session,
        )

    # El tope se aplica antes de pagar y no deja la reserva colgada.
    assert provider.call_count == MAX_PRODUCT_SEARCH_ATTEMPTS
    assert db_session.query(ProductSearch).count() == MAX_PRODUCT_SEARCH_ATTEMPTS
    assert db_session.query(RegenerationLease).count() == 0

    detail = get_persisted_outfit(outfit.id, db_session)
    assert detail is not None
    assert detail.product_search_items[0].attempts_remaining == 0


def test_empty_result_is_cached_instead_of_spending_again(db_session, monkeypatch):
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result())
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    first = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(),
        db_session,
    )
    second = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(),
        db_session,
    )

    assert first is not None and first.created is True
    assert first.search.candidates == []
    assert second is not None and second.created is False
    provider.assert_called_once()


def test_insufficient_item_and_active_operation_block_before_provider(
    db_session,
    monkeypatch,
):
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result())
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    with pytest.raises(ProductSearchInputError):
        search_persisted_outfit_item(
            outfit.id,
            1,
            ProductSearchRequest(),
            db_session,
        )

    db_session.add(
        RegenerationLease(
            outfit_id=outfit.id,
            token="image-operation",
            acquired_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    with pytest.raises(OutfitOperationInProgressError):
        search_persisted_outfit_item(
            outfit.id,
            0,
            ProductSearchRequest(),
            db_session,
        )

    provider.assert_not_called()


def test_provider_failure_releases_reservation_without_persisting(
    db_session,
    monkeypatch,
):
    outfit = _persist_outfit(db_session)
    provider = Mock(side_effect=ProductSearchProviderError("provider failed"))
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    with pytest.raises(ProductSearchProviderError):
        search_persisted_outfit_item(
            outfit.id,
            0,
            ProductSearchRequest(),
            db_session,
        )

    provider.assert_called_once()
    assert db_session.query(ProductSearch).count() == 0
    assert db_session.query(RegenerationLease).count() == 0


def test_two_concurrent_searches_only_call_provider_once(tmp_path, monkeypatch):
    """Dos sesiones compiten por la reserva antes de iniciar una búsqueda pagada."""
    database_path = tmp_path / "product-search-race.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    with TestingSession() as setup_db:
        outfit = _persist_outfit(setup_db)
        outfit_id = outfit.id

    search_started = threading.Event()
    allow_search_to_finish = threading.Event()
    provider_queries: list[str] = []
    worker_results = []
    worker_errors = []

    def slow_search(query, *, brand=None):
        provider_queries.append(query)
        assert brand is None
        search_started.set()
        if not allow_search_to_finish.wait(timeout=5):
            raise AssertionError("La prueba no liberó la búsqueda bloqueada")
        return _provider_result()

    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        slow_search,
    )

    def first_request():
        with TestingSession() as worker_db:
            try:
                worker_results.append(
                    search_persisted_outfit_item(
                        outfit_id,
                        0,
                        ProductSearchRequest(),
                        worker_db,
                    )
                )
            except Exception as exc:  # Se comprueba tras hacer join.
                worker_errors.append(exc)

    worker = threading.Thread(target=first_request)
    try:
        worker.start()
        assert search_started.wait(timeout=5)

        with TestingSession() as competing_db:
            with pytest.raises(OutfitOperationInProgressError):
                search_persisted_outfit_item(
                    outfit_id,
                    0,
                    ProductSearchRequest(),
                    competing_db,
                )

        assert len(provider_queries) == 1
        allow_search_to_finish.set()
        worker.join(timeout=5)
    finally:
        allow_search_to_finish.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert worker_errors == []
    assert len(worker_results) == 1
    assert worker_results[0] is not None and worker_results[0].created is True

    with TestingSession() as verification_db:
        assert verification_db.query(ProductSearch).filter_by(outfit_id=outfit_id).count() == 1
        assert verification_db.query(RegenerationLease).count() == 0
    engine.dispose()


def test_generic_item_accepts_and_persists_user_details(db_session, monkeypatch):
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result())
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    result = search_persisted_outfit_item(
        outfit.id,
        2,
        ProductSearchRequest(additional_details="bolso pequeño de piel"),
        db_session,
    )

    assert result is not None
    assert result.search.additional_details == "bolso pequeño de piel"
    assert result.search.query == "bolso pequeño de piel verde comprar online España"
    assert db_session.query(ProductSearch).one().additional_details == ("bolso pequeño de piel")


def test_brand_written_in_details_is_sent_as_a_hard_search_constraint(
    db_session,
    monkeypatch,
):
    outfit = _persist_outfit(db_session)
    provider = Mock(return_value=_provider_result())
    monkeypatch.setattr(
        "app.services.product_search_service.search_products",
        provider,
    )

    result = search_persisted_outfit_item(
        outfit.id,
        0,
        ProductSearchRequest(additional_details="Versace, verde militar"),
        db_session,
    )

    assert result is not None
    assert result.search.query == ("camisa Versace blanca lino verde militar comprar online España")
    provider.assert_called_once_with(
        "camisa Versace blanca lino verde militar comprar online España",
        brand="Versace",
    )
