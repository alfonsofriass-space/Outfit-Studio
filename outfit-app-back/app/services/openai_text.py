import json
import logging

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
from app.prompts.text_system_prompt import SYSTEM_PROMPT
from app.schemas import OutfitExtraction
from app.validation import needs_fallback

logger = logging.getLogger(__name__)


class TextGenerationError(Exception):
    """Fallo controlado del flujo de extracción de texto."""


class TextServiceUnavailableError(TextGenerationError):
    """OpenAI no está disponible o la cuenta no puede completar la petición."""


class TextProviderError(TextGenerationError):
    """OpenAI rechazó la petición o devolvió una respuesta de protocolo inválida."""


class TextModelOutputError(TextGenerationError):
    """El modelo devolvió una extracción ausente, inválida o de calidad insuficiente."""


_SERVICE_UNAVAILABLE_ERRORS = (
    APIConnectionError,  # Incluye APITimeoutError.
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    InternalServerError,
)


def _get_client() -> OpenAI:
    # Sin timeout explícito, el default del SDK es 600 s con 2 reintentos:
    # workers colgados 10 minutos y llamadas facturadas por duplicado.
    settings = get_settings()
    return OpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_timeout_text,
        max_retries=1,
    )


def _call_openai_text(description: str, model: str) -> OutfitExtraction:
    """Llama a OpenAI y exige una extracción Pydantic realmente parseada."""
    client = _get_client()
    try:
        # Namespace estable (openai>=1.40); el antiguo client.beta.* está deprecado.
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
            response_format=OutfitExtraction,
            temperature=0.2,  # Baja temperatura para que sea predecible.
        )
    except (json.JSONDecodeError, ValidationError, LengthFinishReasonError) as exc:
        # Estos fallos describen una salida de modelo no parseable. Un modelo más
        # capaz sí puede mejorarla, a diferencia de auth/rate limit/transporte.
        raise TextModelOutputError(
            f"El modelo {model} no devolvió una extracción estructurada válida."
        ) from exc

    if not completion.choices:
        raise TextModelOutputError(f"El modelo {model} devolvió una respuesta sin opciones.")

    message = completion.choices[0].message
    if message.parsed is None:
        # Una negativa del modelo no debe intentarse sortear llamando a otro modelo.
        if message.refusal:
            raise TextProviderError(f"El modelo {model} rechazó procesar la descripción.")
        raise TextModelOutputError(f"El modelo {model} devolvió una extracción vacía.")

    if not isinstance(message.parsed, OutfitExtraction):
        raise TextModelOutputError(f"El modelo {model} devolvió un tipo de extracción inesperado.")

    return message.parsed


def _call_model(description: str, model: str) -> OutfitExtraction:
    """Traduce errores del SDK a errores de dominio sin decidir el fallback."""
    try:
        return _call_openai_text(description, model)
    except TextGenerationError:
        raise
    except _SERVICE_UNAVAILABLE_ERRORS as exc:
        logger.error(
            "El modelo %s no está disponible (%s).",
            model,
            type(exc).__name__,
        )
        raise TextServiceUnavailableError(
            f"El servicio del modelo {model} no está disponible."
        ) from exc
    except OpenAIError as exc:
        logger.error(
            "OpenAI rechazó la llamada al modelo %s (%s).",
            model,
            type(exc).__name__,
        )
        raise TextProviderError(
            f"OpenAI no pudo procesar la petición con el modelo {model}."
        ) from exc


def _requires_better_model(extraction: OutfitExtraction) -> bool:
    """Evalúa si una salida no terminal necesita un modelo más capaz.

    El fallback existe para salidas que el código no puede usar, no para salidas
    usables que el producto rechaza. Una extracción bien formada que no supera el
    contrato mínimo es una lectura fiel de un texto con pocas prendas: mini leería
    ese mismo texto y devolvería lo mismo, igual que en `needs_clarification`. El
    contrato lo aplica `process_outfit_request`, que puede pedir una aclaración
    concreta en vez de gastar una segunda llamada y acabar en un error 502.
    """
    if extraction.status == "needs_clarification":
        return False

    return needs_fallback(extraction)


def _call_and_validate_fallback(
    description: str,
    model_fallback: str,
) -> OutfitExtraction:
    """Llama exactamente una vez al fallback y exige una salida terminal usable."""
    logger.info("Llamando al modelo de fallback: %s", model_fallback)
    extraction = _call_model(description, model_fallback)

    if _requires_better_model(extraction):
        raise TextModelOutputError(
            f"El modelo de fallback {model_fallback} tampoco devolvió una extracción usable."
        )

    return extraction


def extract_outfit_from_text(description: str) -> tuple[OutfitExtraction, str, str | None]:
    """
    Llama al modelo principal y usa el fallback como máximo una vez, solo cuando
    la salida estructurada es inválida o no cumple los criterios de calidad.

    Los errores de autenticación, permisos, rate limit, transporte o proveedor
    nunca activan el fallback: otro modelo sufriría el mismo fallo operativo.
    """
    settings = get_settings()
    model_primary = settings.openai_text_model_primary
    model_fallback = settings.openai_text_model_fallback

    logger.info("Llamando al modelo principal: %s", model_primary)
    try:
        extraction = _call_model(description, model_primary)
    except TextModelOutputError as exc:
        logger.warning(
            "La salida del modelo principal no se pudo usar; aplicando fallback (%s).",
            type(exc).__name__,
        )
        fallback_extraction = _call_and_validate_fallback(description, model_fallback)
        return fallback_extraction, model_primary, model_fallback

    if not _requires_better_model(extraction):
        return extraction, model_primary, None

    logger.warning("El resultado del modelo principal no cumple los criterios de calidad.")
    fallback_extraction = _call_and_validate_fallback(description, model_fallback)
    return fallback_extraction, model_primary, model_fallback
