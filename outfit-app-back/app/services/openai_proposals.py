import json
import logging
from typing import NamedTuple

from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from app.config import get_settings
from app.prompts.proposal_system_prompt import PROPOSAL_SYSTEM_PROMPT
from app.schemas import ProposalSetExtraction
from app.validation import is_outfit_valid

logger = logging.getLogger(__name__)

# La extracción usa 0.2 porque su trabajo es ser fiel a un texto. Proponer es lo
# contrario: el producto exige tres opciones distintas entre sí, y una temperatura
# baja devuelve la misma silueta tres veces. P10C puede ajustar este valor con
# evidencia real; hasta entonces es la única diferencia deliberada con la extracción.
PROPOSAL_TEMPERATURE = 0.7


class ProposalGenerationError(Exception):
    """Fallo controlado del flujo de propuestas."""


class ProposalServiceUnavailableError(ProposalGenerationError):
    """OpenAI no está disponible o la cuenta no puede completar la petición."""


class ProposalProviderError(ProposalGenerationError):
    """OpenAI rechazó la petición o devolvió una respuesta de protocolo inválida."""


class ProposalModelOutputError(ProposalGenerationError):
    """El modelo devolvió unas propuestas ausentes, inválidas o inusables."""


_SERVICE_UNAVAILABLE_ERRORS = (
    APIConnectionError,  # Incluye APITimeoutError.
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    InternalServerError,
)


class ProposalCallResult(NamedTuple):
    extraction: ProposalSetExtraction
    model_primary: str
    model_fallback: str | None
    input_tokens: int
    output_tokens: int


class _RawCall(NamedTuple):
    extraction: ProposalSetExtraction
    input_tokens: int
    output_tokens: int


def _get_client() -> OpenAI:
    # Mismos motivos que en openai_text: sin timeout explícito el SDK espera 600 s
    # con reintentos, es decir, workers colgados y llamadas facturadas por duplicado.
    settings = get_settings()
    return OpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_timeout_text,
        max_retries=1,
    )


def _usage_tokens(completion: object) -> tuple[int, int]:
    """Lee el usage real. Si el proveedor no lo envía, cero: no se inventa un coste."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "completion_tokens", 0) or 0
    )


def _call_openai_proposals(situation: str, model: str) -> _RawCall:
    """Llama a OpenAI y exige un conjunto de propuestas realmente parseado."""
    client = _get_client()
    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": PROPOSAL_SYSTEM_PROMPT},
                {"role": "user", "content": situation},
            ],
            response_format=ProposalSetExtraction,
            temperature=PROPOSAL_TEMPERATURE,
        )
    except (json.JSONDecodeError, ValidationError, LengthFinishReasonError) as exc:
        # Incluye el recuento incorrecto de propuestas: lo valida el schema, así que
        # una salida con dos o cuatro opciones llega aquí como salida no parseable.
        raise ProposalModelOutputError(
            f"El modelo {model} no devolvió un conjunto de propuestas válido."
        ) from exc

    if not completion.choices:
        raise ProposalModelOutputError(f"El modelo {model} devolvió una respuesta sin opciones.")

    message = completion.choices[0].message
    if message.parsed is None:
        # Una negativa del modelo no debe intentarse sortear llamando a otro modelo.
        if message.refusal:
            raise ProposalProviderError(f"El modelo {model} rechazó procesar la situación.")
        raise ProposalModelOutputError(f"El modelo {model} devolvió unas propuestas vacías.")

    if not isinstance(message.parsed, ProposalSetExtraction):
        raise ProposalModelOutputError(f"El modelo {model} devolvió un tipo de salida inesperado.")

    input_tokens, output_tokens = _usage_tokens(completion)
    return _RawCall(message.parsed, input_tokens, output_tokens)


def _call_model(situation: str, model: str) -> _RawCall:
    """Traduce errores del SDK a errores de dominio sin decidir el fallback."""
    try:
        return _call_openai_proposals(situation, model)
    except ProposalGenerationError:
        raise
    except _SERVICE_UNAVAILABLE_ERRORS as exc:
        logger.error(
            "El modelo %s no está disponible (%s).",
            model,
            type(exc).__name__,
        )
        raise ProposalServiceUnavailableError(
            f"El servicio del modelo {model} no está disponible."
        ) from exc
    except OpenAIError as exc:
        logger.error(
            "OpenAI rechazó la llamada al modelo %s (%s).",
            model,
            type(exc).__name__,
        )
        raise ProposalProviderError(
            f"OpenAI no pudo procesar la petición con el modelo {model}."
        ) from exc


def _requires_better_model(extraction: ProposalSetExtraction) -> bool:
    """Evalúa si la salida es inusable y merece un modelo más capaz.

    Una aclaración es terminal, igual que en la extracción: mini leería la misma
    situación. Lo que sí justifica el fallback es una propuesta que el usuario podría
    elegir y que después no se podría generar, porque dejaría la vía en un callejón
    sin salida justo después de haber pagado.
    """
    if extraction.status == "needs_clarification":
        return False

    for proposal in extraction.proposals:
        try:
            candidate = proposal.to_extraction()
        except ValidationError:
            return True
        is_valid, _ = is_outfit_valid(candidate)
        if not is_valid:
            return True

    return False


def _call_and_validate_fallback(situation: str, model_fallback: str) -> _RawCall:
    """Llama exactamente una vez al fallback y exige una salida terminal usable."""
    logger.info("Llamando al modelo de propuestas de fallback: %s", model_fallback)
    result = _call_model(situation, model_fallback)

    if _requires_better_model(result.extraction):
        raise ProposalModelOutputError(
            f"El modelo de fallback {model_fallback} tampoco devolvió propuestas usables."
        )

    return result


def propose_outfits_from_situation(situation: str) -> ProposalCallResult:
    """
    Llama al modelo de propuestas y usa el fallback como máximo una vez, solo cuando
    la salida estructurada es inválida o alguna propuesta no sería generable.

    Los errores de autenticación, permisos, rate limit, transporte o proveedor nunca
    activan el fallback: otro modelo sufriría el mismo fallo operativo.
    """
    settings = get_settings()
    model_primary = settings.openai_proposal_model
    model_fallback = settings.openai_proposal_fallback_model

    logger.info("Llamando al modelo de propuestas: %s", model_primary)
    try:
        result = _call_model(situation, model_primary)
    except ProposalModelOutputError as exc:
        logger.warning(
            "La salida del modelo de propuestas no se pudo usar; aplicando fallback (%s).",
            type(exc).__name__,
        )
        fallback = _call_and_validate_fallback(situation, model_fallback)
        return ProposalCallResult(
            fallback.extraction,
            model_primary,
            model_fallback,
            fallback.input_tokens,
            fallback.output_tokens,
        )

    if not _requires_better_model(result.extraction):
        return ProposalCallResult(
            result.extraction,
            model_primary,
            None,
            result.input_tokens,
            result.output_tokens,
        )

    logger.warning("Las propuestas del modelo principal no serían todas generables.")
    fallback = _call_and_validate_fallback(situation, model_fallback)
    return ProposalCallResult(
        fallback.extraction,
        model_primary,
        model_fallback,
        fallback.input_tokens,
        fallback.output_tokens,
    )
