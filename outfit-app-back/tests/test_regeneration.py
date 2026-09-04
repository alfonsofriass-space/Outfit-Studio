import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import Base
from app.models import Image, Outfit, RegenerationLease
from app.pricing import estimate_image_cost
from app.schemas import (
    ImageDetails,
    OutfitExtraction,
    OutfitItem,
    OutfitRequest,
)
from app.services import outfit_service
from app.services.openai_image import ImageGenerationError


def _fake_extraction() -> OutfitExtraction:
    return OutfitExtraction(
        status="ok",
        outfit_summary="look casual",
        items=[
            OutfitItem(
                category="upper",
                item_type="chaqueta",
                certainty="high",
                visual_phrase_en="black jacket",
            ),
            OutfitItem(
                category="lower",
                item_type="vaqueros",
                certainty="high",
                visual_phrase_en="wide-leg jeans",
            ),
        ],
    )


def _fake_image(path="/images/fake.png") -> ImageDetails:
    return ImageDetails(model="gpt-image-2", quality="low", size="1024x1024", url_or_base64=path)


def _create_outfit(db):
    """Crea un outfit con su imagen original vía el flujo real, mockeando OpenAI."""
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(_fake_extraction(), "gpt-5.4-nano", None),
        ) as text_mock,
        patch.object(outfit_service, "generate_outfit_image", return_value=_fake_image()),
    ):
        resp = outfit_service.process_outfit_request(
            OutfitRequest(user_description="chaqueta negra y vaqueros anchos"), db
        )
    return resp, text_mock


def test_generate_persists_outfit_and_image(db_session):
    resp, _ = _create_outfit(db_session)
    assert resp.status == "completed"
    assert resp.outfit_id is not None
    # una única imagen (la original) tras la generación
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 1


def test_analysis_only_returns_prompt_without_generating_image(db_session):
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(_fake_extraction(), "gpt-5.4-nano", None),
        ),
        patch.object(outfit_service, "generate_outfit_image") as image_mock,
    ):
        response = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="chaqueta negra y vaqueros anchos",
                generate_image=False,
            ),
            db_session,
        )

    assert response.status == "completed"
    assert response.user_description == "chaqueta negra y vaqueros anchos"
    assert response.image is None
    assert "Top area: black jacket" in response.image_prompt
    settings = get_settings()
    assert response.flat_lay_estimated_cost == estimate_image_cost(
        settings.image_quality,
        settings.image_size,
    )
    image_mock.assert_not_called()
    assert db_session.get(Outfit, response.outfit_id) is not None
    assert db_session.query(Image).filter_by(outfit_id=response.outfit_id).count() == 0


def test_analysis_persists_explicit_styling_in_json_and_reviewed_prompt(db_session):
    extraction = _fake_extraction()
    extraction.styling_notes_en = ["red scarf tied around the waist as a belt"]

    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(extraction, "gpt-5.4-nano", None),
        ),
        patch.object(outfit_service, "generate_outfit_image") as image_mock,
    ):
        response = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="chaqueta, vaqueros y bufanda roja usada como cinturón",
                generate_image=False,
            ),
            db_session,
        )

    image_mock.assert_not_called()
    assert "red scarf tied around the waist as a belt" in response.image_prompt
    assert (
        "except where an explicit styling relationship above requires it" in response.image_prompt
    )
    persisted = db_session.get(Outfit, response.outfit_id)
    assert json.loads(persisted.outfit_json)["styling_notes_en"] == extraction.styling_notes_en


def test_first_image_after_analysis_uses_exact_reviewed_prompt(db_session):
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(_fake_extraction(), "gpt-5.4-nano", None),
        ),
        patch.object(outfit_service, "generate_outfit_image"),
    ):
        analysis = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="chaqueta negra y vaqueros anchos",
                generate_image=False,
            ),
            db_session,
        )

    with patch.object(
        outfit_service,
        "generate_outfit_image",
        return_value=_fake_image("/images/confirmed.png"),
    ) as image_mock:
        result = outfit_service.regenerate_outfit_image(analysis.outfit_id, db_session)

    image_mock.assert_called_once_with(analysis.image_prompt)
    assert "Alternative composition" not in result.generation_prompt
    assert result.regeneration_count == 0
    assert result.regenerations_remaining == 3
    persisted = db_session.query(Image).filter_by(outfit_id=analysis.outfit_id).one()
    assert persisted.generation_prompt == analysis.image_prompt


def test_input_without_clothing_signal_spends_nothing(db_session):
    """Único rechazo previo al modelo: ni prenda ni atributo visual en el texto."""
    with (
        patch.object(outfit_service, "extract_outfit_from_text") as text_mock,
        patch.object(outfit_service, "generate_outfit_image") as image_mock,
    ):
        response = outfit_service.process_outfit_request(
            OutfitRequest(user_description="algo bonito para el finde"), db_session
        )

    assert response.status == "needs_clarification"
    text_mock.assert_not_called()
    image_mock.assert_not_called()
    assert db_session.query(Outfit).count() == 0


def test_bare_strong_item_requests_detail_after_extraction(db_session):
    """`abrigo` ya llega al modelo, pero sigue sin generar imagen ni escribir en BD.

    El diccionario no puede saber si "abrigo y babuchas" contiene una segunda prenda,
    así que el contrato lo aplica `is_outfit_valid` sobre la extracción real.
    """
    extraction = OutfitExtraction(
        status="ok",
        outfit_summary="un abrigo",
        items=[
            OutfitItem(
                category="upper",
                item_type="abrigo",
                certainty="high",
                visual_phrase_en="coat",
            )
        ],
    )
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(extraction, "gpt-5.4-nano", None),
        ) as text_mock,
        patch.object(outfit_service, "generate_outfit_image") as image_mock,
    ):
        response = outfit_service.process_outfit_request(
            OutfitRequest(user_description="abrigo"), db_session
        )

    assert response.status == "needs_clarification"
    assert "color, material, corte o detalle visual" in response.message
    text_mock.assert_called_once()
    image_mock.assert_not_called()
    assert db_session.query(Outfit).count() == 0


def test_editing_a_description_reuses_the_pending_analysis(db_session):
    """Editar no debe dejar un análisis huérfano ni duplicar filas casi idénticas."""
    with patch.object(
        outfit_service,
        "extract_outfit_from_text",
        return_value=(_fake_extraction(), "gpt-5.4-nano", None),
    ):
        first = outfit_service.process_outfit_request(
            OutfitRequest(user_description="chaqueta y vaqueros", generate_image=False),
            db_session,
        )
        second = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="chaqueta negra y vaqueros anchos",
                generate_image=False,
                replace_outfit_id=first.outfit_id,
            ),
            db_session,
        )

    assert second.outfit_id == first.outfit_id
    assert db_session.query(Outfit).count() == 1
    reused = db_session.get(Outfit, first.outfit_id)
    assert reused.user_description == "chaqueta negra y vaqueros anchos"


def test_replacement_never_overwrites_an_analysis_with_paid_results(db_session):
    """Un outfit con imagen ya pagada nunca se reescribe: se crea una fila nueva."""
    created, _ = _create_outfit(db_session)

    with patch.object(
        outfit_service,
        "extract_outfit_from_text",
        return_value=(_fake_extraction(), "gpt-5.4-nano", None),
    ):
        replacement = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="camisa azul y falda negra",
                generate_image=False,
                replace_outfit_id=created.outfit_id,
            ),
            db_session,
        )

    assert replacement.outfit_id != created.outfit_id
    assert db_session.query(Outfit).count() == 2
    original = db_session.get(Outfit, created.outfit_id)
    assert original.user_description == "chaqueta negra y vaqueros anchos"
    assert db_session.query(Image).filter_by(outfit_id=created.outfit_id).count() == 1


def test_replacement_ignores_an_outfit_owned_by_someone_else(db_session):
    from app.models import User

    owner = User(username="otra", password_hash="h", role="user", is_active=True)
    db_session.add(owner)
    db_session.flush()
    foreign = Outfit(
        owner_id=owner.id,
        user_description="outfit ajeno",
        outfit_json=_fake_extraction().model_dump_json(),
        image_prompt="prompt",
        text_model="gpt-5.4-nano",
    )
    db_session.add(foreign)
    db_session.commit()

    intruder = User(username="intruso", password_hash="h", role="user", is_active=True)
    db_session.add(intruder)
    db_session.flush()

    with patch.object(
        outfit_service,
        "extract_outfit_from_text",
        return_value=(_fake_extraction(), "gpt-5.4-nano", None),
    ):
        response = outfit_service.process_outfit_request(
            OutfitRequest(
                user_description="chaqueta y vaqueros",
                generate_image=False,
                replace_outfit_id=foreign.id,
            ),
            db_session,
            intruder,
        )

    assert response.outfit_id != foreign.id
    assert db_session.get(Outfit, foreign.id).user_description == "outfit ajeno"


def test_generate_persists_builder_prompt(db_session):
    """El outfit guarda el prompt compuesto por el builder (zonas corporales), no prosa del LLM."""
    resp, _ = _create_outfit(db_session)
    outfit = db_session.get(Outfit, resp.outfit_id)
    assert "Top area: black jacket" in outfit.image_prompt
    assert "Middle area" in outfit.image_prompt
    assert "worn on a body" in outfit.image_prompt
    image = db_session.query(Image).filter_by(outfit_id=resp.outfit_id).one()
    assert image.generation_prompt == outfit.image_prompt


def test_regeneration_does_not_call_text_model(db_session):
    resp, _ = _create_outfit(db_session)

    with (
        patch.object(outfit_service, "extract_outfit_from_text") as text_mock,
        patch.object(
            outfit_service, "generate_outfit_image", return_value=_fake_image("/images/regen.png")
        ) as image_mock,
    ):
        result = outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)

    assert result.status == "regenerated"
    assert result.regeneration_count == 1
    assert result.regenerations_remaining == 2
    text_mock.assert_not_called()  # regenerar NO vuelve a estructurar el texto

    # Regenerar reutiliza el prompt guardado + directiva de variación (sin añadir prendas)
    (prompt_used,) = image_mock.call_args.args
    assert "Top area: black jacket" in prompt_used
    assert "Alternative composition" in prompt_used
    assert result.generation_prompt == prompt_used
    persisted = (
        db_session.query(Image)
        .filter_by(outfit_id=resp.outfit_id)
        .order_by(Image.id.desc())
        .first()
    )
    assert persisted.generation_prompt == prompt_used


def test_outfit_detail_keeps_generation_order_and_exact_prompts(db_session):
    draft, _ = _create_outfit(db_session)
    db_session.query(Image).filter_by(outfit_id=draft.outfit_id).delete()
    db_session.commit()

    generated, _ = _create_outfit(db_session)
    with patch.object(
        outfit_service,
        "generate_outfit_image",
        return_value=_fake_image("/images/gallery-regen.png"),
    ):
        outfit_service.regenerate_outfit_image(generated.outfit_id, db_session)

    detail = outfit_service.get_persisted_outfit(generated.outfit_id, db_session)

    assert detail is not None
    assert detail.user_description == "chaqueta negra y vaqueros anchos"
    assert [item.generation_number for item in detail.images] == [1, 2]
    assert detail.images[0].generation_prompt == generated.image_prompt
    assert detail.images[1].generation_prompt.endswith("do not add or remove any piece.")
    assert all(item.image.url_or_base64.startswith("/images/") for item in detail.images)


def test_regeneration_limit_reached(db_session):
    resp, _ = _create_outfit(db_session)

    with patch.object(
        outfit_service, "generate_outfit_image", return_value=_fake_image("/images/regen.png")
    ):
        # 3 regeneraciones permitidas
        for expected_remaining in (2, 1, 0):
            result = outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)
            assert result.status == "regenerated"
            assert result.regenerations_remaining == expected_remaining

        # la 4ª debe bloquearse
        blocked = outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)

    assert blocked.status == "regeneration_limit_reached"
    assert blocked.regeneration_count == 3
    # nunca se supera 1 original + 3 regeneraciones = 4 imágenes
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 4


def test_regeneration_outfit_not_found(db_session):
    result = outfit_service.regenerate_outfit_image(9999, db_session)
    assert result is None


def test_image_generated_before_touching_db(db_session):
    """
    Fix F5: la generación de imagen (16-43 s) ocurre ANTES de encolar escrituras
    en la sesión. Si hubiera un INSERT pendiente durante la llamada, SQLite
    retendría el lock de escritura y peticiones concurrentes fallarían con
    'database is locked'.
    """

    def _assert_session_clean(prompt):
        assert not db_session.new, "hay INSERTs pendientes durante la generación de imagen"
        assert not db_session.dirty, "hay UPDATEs pendientes durante la generación de imagen"
        return _fake_image()

    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(_fake_extraction(), "gpt-5.4-nano", None),
        ),
        patch.object(outfit_service, "generate_outfit_image", side_effect=_assert_session_clean),
    ):
        resp = outfit_service.process_outfit_request(
            OutfitRequest(user_description="chaqueta negra y vaqueros anchos"), db_session
        )

    assert resp.status == "completed"
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 1


# ==========================================
# Fallo de generación de imagen (fix del hallazgo F1 de la auditoría)
# ==========================================


def test_image_failure_keeps_outfit_without_fake_image_row(db_session):
    """
    Si la imagen falla al crear el outfit: el análisis se guarda, la respuesta es
    'completed' con image=None + image_error, y NO se persiste ninguna fila de
    imagen (ni path falso, ni coste fantasma, ni regeneración consumida).
    """
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(_fake_extraction(), "gpt-5.4-nano", None),
        ),
        patch.object(
            outfit_service, "generate_outfit_image", side_effect=ImageGenerationError("boom")
        ),
    ):
        resp = outfit_service.process_outfit_request(
            OutfitRequest(user_description="chaqueta negra y vaqueros anchos"), db_session
        )

    assert resp.status == "completed"
    assert resp.outfit_id is not None
    assert resp.image is None
    assert resp.image_error is not None
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 0

    # El outfit quedó usable: una regeneración posterior genera su primera imagen
    with patch.object(outfit_service, "generate_outfit_image", return_value=_fake_image()):
        result = outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)
    assert result.status == "regenerated"
    assert result.regeneration_count == 0
    assert result.regenerations_remaining == 3

    # La siguiente imagen sí es la primera regeneración real.
    with patch.object(
        outfit_service,
        "generate_outfit_image",
        return_value=_fake_image("/images/first-real-regen.png"),
    ):
        first_regeneration = outfit_service.regenerate_outfit_image(
            resp.outfit_id,
            db_session,
        )
    assert first_regeneration.regeneration_count == 1
    assert first_regeneration.regenerations_remaining == 2


def test_failed_regeneration_does_not_consume_limit(db_session):
    """Un intento de regeneración fallido propaga la excepción y no persiste nada."""
    resp, _ = _create_outfit(db_session)

    with patch.object(
        outfit_service, "generate_outfit_image", side_effect=ImageGenerationError("boom")
    ):
        with pytest.raises(ImageGenerationError):
            outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)

    # sigue habiendo solo la imagen original: el fallo no consumió el límite
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 1
    assert db_session.query(RegenerationLease).count() == 0

    with patch.object(outfit_service, "generate_outfit_image", return_value=_fake_image()):
        result = outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)
    assert result.regeneration_count == 1
    assert result.regenerations_remaining == 2


def test_active_regeneration_lease_blocks_before_spending(db_session):
    resp, _ = _create_outfit(db_session)
    db_session.add(
        RegenerationLease(
            outfit_id=resp.outfit_id,
            token="active-request",
            acquired_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    with patch.object(outfit_service, "generate_outfit_image") as image_mock:
        with pytest.raises(outfit_service.RegenerationInProgressError):
            outfit_service.regenerate_outfit_image(resp.outfit_id, db_session)

    image_mock.assert_not_called()
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 1
    assert db_session.query(RegenerationLease).count() == 1


def test_stale_regeneration_lease_is_reclaimed(db_session):
    resp, _ = _create_outfit(db_session)
    stale_at = (
        datetime.now(timezone.utc) - outfit_service.REGENERATION_LEASE_TTL - timedelta(seconds=1)
    )
    db_session.add(
        RegenerationLease(
            outfit_id=resp.outfit_id,
            token="crashed-worker",
            acquired_at=stale_at,
        )
    )
    db_session.commit()

    with patch.object(
        outfit_service,
        "generate_outfit_image",
        return_value=_fake_image("/images/recovered.png"),
    ):
        result = outfit_service.regenerate_outfit_image(
            resp.outfit_id,
            db_session,
        )

    assert result.regeneration_count == 1
    assert db_session.query(RegenerationLease).count() == 0
    assert db_session.query(Image).filter_by(outfit_id=resp.outfit_id).count() == 2


def test_two_concurrent_requests_only_generate_one_image(tmp_path):
    """Dos sesiones reales compiten por la PK; la segunda no llama a OpenAI."""
    database_path = tmp_path / "regeneration-race.db"
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
        outfit = Outfit(
            user_description="chaqueta negra y vaqueros",
            outfit_json="{}",
            image_prompt="saved prompt",
            text_model="test-text",
        )
        setup_db.add(outfit)
        setup_db.flush()
        outfit_id = outfit.id
        setup_db.add(
            Image(
                outfit_id=outfit_id,
                path="/images/original.png",
                image_model="test-image",
                quality="low",
                size="1024x1024",
                cost_estimate=0.006,
            )
        )
        setup_db.commit()

    generation_started = threading.Event()
    allow_generation_to_finish = threading.Event()
    image_calls: list[str] = []
    worker_results = []
    worker_errors = []

    def slow_generate(prompt):
        image_calls.append(prompt)
        generation_started.set()
        if not allow_generation_to_finish.wait(timeout=5):
            raise AssertionError("La prueba no liberó la generación bloqueada")
        return _fake_image("/images/concurrent-winner.png")

    def first_request():
        with TestingSession() as worker_db:
            try:
                worker_results.append(
                    outfit_service.regenerate_outfit_image(
                        outfit_id,
                        worker_db,
                    )
                )
            except Exception as exc:  # Se comprueba tras hacer join.
                worker_errors.append(exc)

    worker = threading.Thread(target=first_request)
    try:
        with patch.object(
            outfit_service,
            "generate_outfit_image",
            side_effect=slow_generate,
        ):
            worker.start()
            assert generation_started.wait(timeout=5)

            with TestingSession() as competing_db:
                with pytest.raises(outfit_service.RegenerationInProgressError):
                    outfit_service.regenerate_outfit_image(
                        outfit_id,
                        competing_db,
                    )

            assert len(image_calls) == 1
            allow_generation_to_finish.set()
            worker.join(timeout=5)
    finally:
        allow_generation_to_finish.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert worker_errors == []
    assert len(worker_results) == 1
    assert worker_results[0].regeneration_count == 1
    assert worker_results[0].regenerations_remaining == 2

    with TestingSession() as verification_db:
        assert verification_db.query(Image).filter_by(outfit_id=outfit_id).count() == 2
        assert verification_db.query(RegenerationLease).count() == 0
    engine.dispose()


def test_response_reports_omitted_accessories(db_session):
    """El cap de accesorios del builder se informa en la respuesta, no se recorta en silencio."""
    extraction = _fake_extraction()
    extraction.items += [
        OutfitItem(
            category="accessory",
            item_type=f"acc{i}",
            certainty="high",
            visual_phrase_en=f"accessory number {i}",
        )
        for i in range(5)
    ]
    with (
        patch.object(
            outfit_service,
            "extract_outfit_from_text",
            return_value=(extraction, "gpt-5.4-nano", None),
        ),
        patch.object(outfit_service, "generate_outfit_image", return_value=_fake_image()),
    ):
        resp = outfit_service.process_outfit_request(
            OutfitRequest(user_description="chaqueta, vaqueros y cinco accesorios"), db_session
        )

    assert resp.accessories_omitted == ["accessory number 4"]
