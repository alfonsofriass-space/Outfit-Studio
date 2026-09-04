import json
import logging

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Outfit, ProposalSet, User
from app.pricing import estimate_image_cost, estimate_text_cost
from app.prompts.image_prompt_builder import build_image_prompt
from app.schemas import (
    ClarificationResponse,
    FinalOutfitResponse,
    ModelsUsed,
    ProposalChoiceRequest,
    ProposalDetails,
    ProposalRequest,
    ProposalSetResponse,
    ProposedOutfit,
)
from app.services.openai_proposals import propose_outfits_from_situation
from app.services.product_search_service import build_product_search_item_states
from app.services.user_operation_lease import (
    acquire_user_operation_lease,
    release_user_operation_lease,
)
from app.validation import SITUATION_SUGGESTION, evaluate_situation_input

logger = logging.getLogger(__name__)

_PROPOSALS_ADAPTER = TypeAdapter(list[ProposedOutfit])

NO_SITUATION_TO_DRESS_MESSAGE = (
    "No he encontrado una situación que vestir en tu texto. Cuéntame el plan y te "
    "propongo tres outfits."
)


class ProposalSetUnavailableError(Exception):
    """El conjunto guardado no se puede leer y no se puede promocionar sin inventar."""


def _can_access(proposal_set: ProposalSet, current_user: User | None) -> bool:
    return (
        current_user is None
        or current_user.role == "admin"
        or proposal_set.owner_id == current_user.id
    )


def _load_proposals(proposal_set: ProposalSet) -> list[ProposedOutfit]:
    try:
        return _PROPOSALS_ADAPTER.validate_json(proposal_set.proposals_json)
    except (ValidationError, ValueError) as exc:
        raise ProposalSetUnavailableError(
            f"El conjunto de propuestas {proposal_set.id} no contiene propuestas legibles."
        ) from exc


def _chosen_indexes(db: Session, proposal_set_id: int) -> list[int]:
    rows = db.scalars(
        select(Outfit.proposal_index)
        .where(Outfit.proposal_set_id == proposal_set_id)
        .order_by(Outfit.proposal_index)
    ).all()
    return [index for index in rows if index is not None]


def _to_response(
    db: Session,
    proposal_set: ProposalSet,
    proposals: list[ProposedOutfit],
    models: ModelsUsed,
) -> ProposalSetResponse:
    return ProposalSetResponse(
        proposal_set_id=proposal_set.id,
        situation=proposal_set.situation,
        proposals=[
            ProposalDetails(
                index=index,
                title=proposal.title,
                outfit_summary=proposal.outfit_summary,
                items=proposal.items,
                styling_notes_en=proposal.styling_notes_en,
            )
            for index, proposal in enumerate(proposals)
        ],
        cost_estimate=proposal_set.cost_estimate,
        chosen_indexes=_chosen_indexes(db, proposal_set.id),
        created_at=proposal_set.created_at,
        models_used=models,
    )


def propose_outfits(
    request: ProposalRequest,
    db: Session,
    current_user: User,
) -> ProposalSetResponse | ClarificationResponse:
    """
    Genera tres propuestas para una situación y las guarda en UNA fila.

    No crea ningún outfit: hacerlo dejaría dos análisis huérfanos por petición. La
    reserva por usuario impide que un doble clic pague dos veces, porque la reserva
    por outfit no puede cubrir una llamada anterior a que exista el outfit.
    """
    situation = " ".join(request.situation.split())

    gate = evaluate_situation_input(situation)
    if not gate.is_sufficient:
        # Rechazo local: ni una llamada, ni una fila.
        return ClarificationResponse(
            message=gate.message or NO_SITUATION_TO_DRESS_MESSAGE,
            suggestion=SITUATION_SUGGESTION,
        )

    lease_token = acquire_user_operation_lease(db, current_user.id)
    try:
        result = propose_outfits_from_situation(situation)

        if result.extraction.status == "needs_clarification":
            # La llamada se pagó, pero no hay propuestas que guardar: persistir un
            # conjunto vacío llenaría la biblioteca de filas sin contenido.
            return ClarificationResponse(
                message=NO_SITUATION_TO_DRESS_MESSAGE,
                suggestion=SITUATION_SUGGESTION,
            )

        model_used = result.model_fallback or result.model_primary
        proposal_set = ProposalSet(
            owner_id=current_user.id,
            situation=situation,
            proposals_json=json.dumps(
                [proposal.model_dump() for proposal in result.extraction.proposals],
                ensure_ascii=False,
            ),
            text_model=model_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            # Coste medido desde el usage devuelto, nunca estimado antes de llamar.
            cost_estimate=estimate_text_cost(
                model_used,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ),
        )
        db.add(proposal_set)
        db.commit()

        response = _to_response(
            db,
            proposal_set,
            list(result.extraction.proposals),
            ModelsUsed(
                text_primary=result.model_primary,
                text_fallback=result.model_fallback,
            ),
        )
    finally:
        release_user_operation_lease(db, current_user.id, lease_token)

    return response


def get_proposal_set(
    proposal_set_id: int,
    db: Session,
    current_user: User | None = None,
) -> ProposalSetResponse | None:
    """Devuelve un conjunto ya pagado. Sin llamadas: reconstruye el paso al recargar."""
    proposal_set = db.get(ProposalSet, proposal_set_id)
    if proposal_set is None or not _can_access(proposal_set, current_user):
        return None

    proposals = _load_proposals(proposal_set)
    return _to_response(
        db,
        proposal_set,
        proposals,
        ModelsUsed(text_primary=proposal_set.text_model),
    )


def choose_proposal(
    proposal_set_id: int,
    request: ProposalChoiceRequest,
    db: Session,
    current_user: User | None = None,
) -> FinalOutfitResponse | None:
    """
    Promociona una propuesta a outfit. **Cero llamadas pagadas**: la extracción ya
    está persistida, así que elegir solo compone el prompt de imagen y escribe la fila.

    Elegir dos veces la misma propuesta devuelve el outfit existente en vez de
    duplicarlo, y elegir otra del mismo conjunto sigue estando permitido: la primera
    elección no puede convertirse en un callejón sin salida.
    """
    proposal_set = db.get(ProposalSet, proposal_set_id)
    if proposal_set is None or not _can_access(proposal_set, current_user):
        return None

    proposals = _load_proposals(proposal_set)
    index = request.proposal_index
    if index >= len(proposals):
        return None

    try:
        extraction = proposals[index].to_extraction()
    except ValidationError as exc:
        raise ProposalSetUnavailableError(
            f"La propuesta {index} del conjunto {proposal_set_id} no es una extracción válida."
        ) from exc

    built = build_image_prompt(extraction.items, extraction.styling_notes_en)

    outfit = db.scalar(
        select(Outfit).where(
            Outfit.proposal_set_id == proposal_set_id,
            Outfit.proposal_index == index,
        )
    )
    if outfit is None:
        outfit = Outfit(
            # El outfit pertenece a quien pagó las propuestas, no a quien las abre:
            # un admin revisando un conjunto ajeno no se apropia del resultado.
            owner_id=proposal_set.owner_id,
            user_description=extraction.outfit_summary,
            outfit_json=extraction.model_dump_json(),
            image_prompt=built.prompt,
            text_model=proposal_set.text_model,
            proposal_set_id=proposal_set_id,
            proposal_index=index,
        )
        db.add(outfit)
        db.commit()
        logger.info(
            "Propuesta %s del conjunto %s promocionada al outfit %s.",
            index,
            proposal_set_id,
            outfit.id,
        )

    settings = get_settings()
    return FinalOutfitResponse(
        outfit_id=outfit.id,
        user_description=outfit.user_description,
        outfit=extraction,
        image=None,
        image_id=None,
        image_prompt=built.prompt,
        flat_lay_estimated_cost=estimate_image_cost(settings.image_quality, settings.image_size),
        accessories_omitted=built.accessories_omitted,
        # Un outfit recién promocionado no tiene imagen ni búsquedas: las consultas
        # base y el presupuesto completo de intentos, igual que un análisis nuevo.
        product_search_items=build_product_search_item_states(extraction, []),
        models_used=ModelsUsed(text_primary=proposal_set.text_model),
    )
