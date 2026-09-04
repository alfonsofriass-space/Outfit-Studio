from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from factories import make_item
from openai import RateLimitError
from sqlalchemy import func, select

from app.models import Outfit, ProposalSet, User, UserOperationLease
from app.pricing import estimate_text_cost
from app.schemas import (
    ClarificationResponse,
    ProposalChoiceRequest,
    ProposalRequest,
    ProposalSetExtraction,
    ProposedOutfit,
)
from app.services import openai_proposals, proposal_service
from app.services.openai_proposals import ProposalCallResult
from app.services.user_operation_lease import (
    UserOperationInProgressError,
    acquire_user_operation_lease,
)
from app.validation import MinimumInfoReason, evaluate_minimum_info

SITUATION = "Boda de tarde en octubre, en el campo, voy de invitado"
PROPOSAL_MODEL = "gpt-5.4-nano"
FALLBACK_MODEL = "gpt-5.4-mini"


def _user(db, username: str, *, role: str = "user") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def _proposal(title: str) -> ProposedOutfit:
    return ProposedOutfit(
        title=title,
        outfit_summary=f"Resumen de {title}",
        items=[
            make_item("upper", "camisa", "blue shirt", color="azul"),
            make_item("lower", "pantalón", "blue trousers", color="azul"),
            make_item("footwear", "zapatos", "blue shoes", color="azul"),
        ],
    )


def _accessories_only_proposal(title: str) -> ProposedOutfit:
    """Propuesta que no supera el contrato mínimo: solo accesorios."""
    return ProposedOutfit(
        title=title,
        outfit_summary="Solo complementos",
        items=[
            make_item("accessory", "cinturón", "blue belt", color="azul"),
            make_item("accessory", "bufanda", "blue scarf", color="azul"),
        ],
    )


def _extraction(*titles: str) -> ProposalSetExtraction:
    return ProposalSetExtraction(status="ok", proposals=[_proposal(title) for title in titles])


def _call_result(
    extraction: ProposalSetExtraction | None = None,
    *,
    fallback: str | None = None,
    input_tokens: int = 1200,
    output_tokens: int = 800,
) -> ProposalCallResult:
    return ProposalCallResult(
        extraction or _extraction("Lino arena", "Chaleco", "Azul noche"),
        PROPOSAL_MODEL,
        fallback,
        input_tokens,
        output_tokens,
    )


def _status_error(error_type, status_code: int):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type("upstream error", response=response, body=None)


def _counts(db) -> tuple[int, int]:
    return (
        db.scalar(select(func.count(ProposalSet.id))),
        db.scalar(select(func.count(Outfit.id))),
    )


# ---------------------------------------------------------------- puerta local


def test_situation_without_letters_is_rejected_before_any_call(db_session):
    user = _user(db_session, "alfonso")

    with patch.object(proposal_service, "propose_outfits_from_situation") as model_call:
        result = proposal_service.propose_outfits(
            ProposalRequest(situation="  1234 !! "), db_session, user
        )

    model_call.assert_not_called()
    assert isinstance(result, ClarificationResponse)
    assert _counts(db_session) == (0, 0)


def test_situation_without_garments_is_accepted_unlike_the_description_lane(db_session):
    """La regresión que define la fase: la puerta de `describir` rechaza una situación."""
    assert evaluate_minimum_info(SITUATION).reason is MinimumInfoReason.NO_CLOTHING_SIGNAL

    user = _user(db_session, "alfonso")
    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(),
    ):
        result = proposal_service.propose_outfits(
            ProposalRequest(situation=SITUATION), db_session, user
        )

    assert result.status == "proposals_ready"
    assert len(result.proposals) == 3


# ------------------------------------------------------------------ persistencia


def test_proposing_persists_one_set_and_zero_outfits(db_session):
    user = _user(db_session, "alfonso")

    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(),
    ):
        result = proposal_service.propose_outfits(
            ProposalRequest(situation=SITUATION), db_session, user
        )

    assert _counts(db_session) == (1, 0)
    assert result.chosen_indexes == []
    assert [proposal.index for proposal in result.proposals] == [0, 1, 2]


def test_cost_is_measured_from_usage_not_estimated(db_session):
    user = _user(db_session, "alfonso")

    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(input_tokens=1200, output_tokens=800),
    ):
        result = proposal_service.propose_outfits(
            ProposalRequest(situation=SITUATION), db_session, user
        )

    expected = estimate_text_cost(PROPOSAL_MODEL, input_tokens=1200, output_tokens=800)
    assert result.cost_estimate == expected
    assert db_session.scalar(select(ProposalSet.input_tokens)) == 1200


def test_clarified_situation_persists_nothing(db_session):
    user = _user(db_session, "alfonso")
    clarification = ProposalSetExtraction(status="needs_clarification", proposals=[])

    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(clarification),
    ):
        result = proposal_service.propose_outfits(
            ProposalRequest(situation="asdf qwerty"), db_session, user
        )

    assert isinstance(result, ClarificationResponse)
    assert _counts(db_session) == (0, 0)


# ------------------------------------------------------------------ concurrencia


def test_a_second_proposal_request_is_rejected_before_calling(db_session):
    user = _user(db_session, "alfonso")
    acquire_user_operation_lease(db_session, user.id)

    with patch.object(proposal_service, "propose_outfits_from_situation") as model_call:
        with pytest.raises(UserOperationInProgressError):
            proposal_service.propose_outfits(ProposalRequest(situation=SITUATION), db_session, user)

    model_call.assert_not_called()
    assert _counts(db_session) == (0, 0)


def test_lease_is_released_after_a_failed_call(db_session):
    user = _user(db_session, "alfonso")

    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        side_effect=openai_proposals.ProposalModelOutputError("inusable"),
    ):
        with pytest.raises(openai_proposals.ProposalModelOutputError):
            proposal_service.propose_outfits(ProposalRequest(situation=SITUATION), db_session, user)

    assert db_session.scalar(select(func.count(UserOperationLease.user_id))) == 0
    assert _counts(db_session) == (0, 0)


def test_each_user_holds_an_independent_lease(db_session):
    first = _user(db_session, "alfonso")
    second = _user(db_session, "otra")
    acquire_user_operation_lease(db_session, first.id)

    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(),
    ):
        result = proposal_service.propose_outfits(
            ProposalRequest(situation=SITUATION), db_session, second
        )

    assert result.status == "proposals_ready"


# ------------------------------------------------- disciplina de fallback y errores


def test_rate_limit_never_calls_a_second_model():
    with patch.object(
        openai_proposals,
        "_call_openai_proposals",
        side_effect=_status_error(RateLimitError, 429),
    ) as model_call:
        with pytest.raises(openai_proposals.ProposalServiceUnavailableError):
            openai_proposals.propose_outfits_from_situation(SITUATION)

    assert model_call.call_count == 1


def test_ungenerable_proposals_trigger_the_fallback_once(monkeypatch):
    settings = openai_proposals.get_settings()
    monkeypatch.setattr(settings, "openai_proposal_model", PROPOSAL_MODEL)
    monkeypatch.setattr(settings, "openai_proposal_fallback_model", FALLBACK_MODEL)

    unusable = ProposalSetExtraction(
        status="ok",
        proposals=[
            _accessories_only_proposal("Solo complementos"),
            _proposal("Válida"),
            _proposal("Otra válida"),
        ],
    )
    usable = _extraction("Una", "Dos", "Tres")

    with patch.object(
        openai_proposals,
        "_call_openai_proposals",
        side_effect=[
            openai_proposals._RawCall(unusable, 10, 10),
            openai_proposals._RawCall(usable, 20, 20),
        ],
    ) as model_call:
        result = openai_proposals.propose_outfits_from_situation(SITUATION)

    assert model_call.call_count == 2
    assert result.model_fallback == FALLBACK_MODEL
    # El usage devuelto es el de la llamada que realmente produjo las propuestas.
    assert (result.input_tokens, result.output_tokens) == (20, 20)


def test_clarification_is_terminal_and_never_calls_the_fallback(monkeypatch):
    settings = openai_proposals.get_settings()
    monkeypatch.setattr(settings, "openai_proposal_model", PROPOSAL_MODEL)

    clarification = ProposalSetExtraction(status="needs_clarification", proposals=[])
    with patch.object(
        openai_proposals,
        "_call_openai_proposals",
        return_value=openai_proposals._RawCall(clarification, 5, 5),
    ) as model_call:
        result = openai_proposals.propose_outfits_from_situation("asdf")

    assert model_call.call_count == 1
    assert result.model_fallback is None


# --------------------------------------------------------------------- elección


def _persisted_set(db, user: User) -> int:
    with patch.object(
        proposal_service,
        "propose_outfits_from_situation",
        return_value=_call_result(),
    ):
        result = proposal_service.propose_outfits(ProposalRequest(situation=SITUATION), db, user)
    return result.proposal_set_id


def test_choosing_creates_one_outfit_without_any_paid_call(db_session):
    user = _user(db_session, "alfonso")
    set_id = _persisted_set(db_session, user)

    with patch.object(proposal_service, "propose_outfits_from_situation") as model_call:
        result = proposal_service.choose_proposal(
            set_id, ProposalChoiceRequest(proposal_index=1), db_session, user
        )

    model_call.assert_not_called()
    assert result.status == "completed"
    assert result.image is None
    assert result.image_prompt
    assert _counts(db_session) == (1, 1)

    outfit = db_session.get(Outfit, result.outfit_id)
    assert outfit.owner_id == user.id
    assert (outfit.proposal_set_id, outfit.proposal_index) == (set_id, 1)


def test_choosing_the_same_proposal_twice_returns_the_same_outfit(db_session):
    user = _user(db_session, "alfonso")
    set_id = _persisted_set(db_session, user)
    request = ProposalChoiceRequest(proposal_index=0)

    first = proposal_service.choose_proposal(set_id, request, db_session, user)
    second = proposal_service.choose_proposal(set_id, request, db_session, user)

    assert first.outfit_id == second.outfit_id
    assert _counts(db_session) == (1, 1)


def test_choosing_a_second_proposal_is_still_allowed(db_session):
    """Elegir una propuesta no puede cerrar las otras dos: sería otro callejón."""
    user = _user(db_session, "alfonso")
    set_id = _persisted_set(db_session, user)

    first = proposal_service.choose_proposal(
        set_id, ProposalChoiceRequest(proposal_index=0), db_session, user
    )
    second = proposal_service.choose_proposal(
        set_id, ProposalChoiceRequest(proposal_index=2), db_session, user
    )

    assert first.outfit_id != second.outfit_id
    assert _counts(db_session) == (1, 2)
    assert proposal_service.get_proposal_set(set_id, db_session, user).chosen_indexes == [0, 2]


def test_choosing_an_index_out_of_range_is_not_found(db_session):
    user = _user(db_session, "alfonso")
    set_id = _persisted_set(db_session, user)

    assert (
        proposal_service.choose_proposal(
            set_id, ProposalChoiceRequest(proposal_index=9), db_session, user
        )
        is None
    )
    assert _counts(db_session) == (1, 0)


# --------------------------------------------------------------------- propiedad


def test_a_foreign_user_can_neither_read_nor_choose(db_session):
    owner = _user(db_session, "alfonso")
    intruder = _user(db_session, "otra")
    set_id = _persisted_set(db_session, owner)

    assert proposal_service.get_proposal_set(set_id, db_session, intruder) is None
    assert (
        proposal_service.choose_proposal(
            set_id, ProposalChoiceRequest(proposal_index=0), db_session, intruder
        )
        is None
    )
    assert _counts(db_session) == (1, 0)


def test_admin_reads_a_foreign_set_without_taking_ownership(db_session):
    owner = _user(db_session, "alfonso")
    admin = _user(db_session, "admin", role="admin")
    set_id = _persisted_set(db_session, owner)

    assert proposal_service.get_proposal_set(set_id, db_session, admin) is not None
    result = proposal_service.choose_proposal(
        set_id, ProposalChoiceRequest(proposal_index=0), db_session, admin
    )

    # El outfit pertenece a quien pagó las propuestas, no a quien las abrió.
    assert db_session.get(Outfit, result.outfit_id).owner_id == owner.id


# ------------------------------------------------- rescate del desajuste de vía


def test_a_situation_written_in_the_description_lane_offers_the_other_one(db_session):
    """El rechazo local existente pasa a ser el descubrimiento del modo nuevo."""
    from app.schemas import OutfitRequest
    from app.services.outfit_service import process_outfit_request

    user = _user(db_session, "alfonso")
    with patch("app.services.outfit_service.extract_outfit_from_text") as model_call:
        result = process_outfit_request(
            OutfitRequest(user_description=SITUATION, generate_image=False),
            db_session,
            user,
        )

    model_call.assert_not_called()
    assert result.status == "needs_clarification"
    assert result.suggested_mode == "inspiration"
    assert _counts(db_session) == (0, 0)


def test_proposal_prompt_forbids_promising_attributes_absent_from_the_items():
    """El smoke de P10C encontró títulos que prometían un estampado inexistente."""
    from app.prompts.proposal_system_prompt import PROPOSAL_SYSTEM_PROMPT

    assert "TÍTULO Y RESUMEN" in PROPOSAL_SYSTEM_PROMPT
    assert "la imagen se compone" in PROPOSAL_SYSTEM_PROMPT
    assert "Si no está en la prenda, no existe." in PROPOSAL_SYSTEM_PROMPT
