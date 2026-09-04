import base64
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from app.config import get_settings
from app.pricing import (
    WORN_VIEW_ESTIMATED_COST,
    estimate_gpt_image_2_token_cost,
    estimate_image_cost,
)
from app.schemas import ImageDetails

logger = logging.getLogger(__name__)


class ImageGenerationError(Exception):
    """No hay una imagen válida que persistir.

    Un timeout puede haber sido procesado por el proveedor; por eso los flujos de
    coste sensible desactivan reintentos automáticos.
    """


@dataclass(frozen=True)
class WornImageGeneration:
    image: ImageDetails
    cost_estimate: float


# Carpeta local donde persistimos las imágenes generadas (MVP).
# En producción se migrará a Supabase Storage / S3.
GENERATED_DIR = Path(__file__).resolve().parents[1] / "generated"


def _get_client(*, max_retries: int = 1) -> OpenAI:
    # Timeout holgado (la generación tarda 16-43 s medidos) pero acotado, y solo
    # 1 reintento: un timeout de red tras generar puede facturar la imagen igual,
    # así que cada reintento extra es una imagen potencialmente pagada de más.
    settings = get_settings()
    return OpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_timeout_image,
        max_retries=max_retries,
    )


def _save_image_b64(b64_data: str) -> str:
    """
    Decodifica el base64 devuelto por OpenAI y lo guarda como PNG en disco local.
    Devuelve la ruta pública propia (/images/<uuid>.png, servida por un endpoint
    que comprueba el propietario) — NUNCA la URL de OpenAI, que caduca ~1h.
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    filepath = GENERATED_DIR / filename
    filepath.write_bytes(base64.b64decode(b64_data))
    logger.info(f"Imagen guardada en {filepath}")
    return f"/images/{filename}"


def resolve_generated_image_path(public_path: str) -> Path:
    """Resuelve una ruta pública propia sin permitir salir de GENERATED_DIR."""
    prefix = "/images/"
    if not public_path.startswith(prefix):
        raise ValueError("La imagen fuente no usa una ruta local válida.")
    filename = public_path.removeprefix(prefix)
    if not filename or Path(filename).name != filename:
        raise ValueError("La imagen fuente contiene una ruta no válida.")

    generated_root = GENERATED_DIR.resolve()
    resolved = (generated_root / filename).resolve()
    if resolved.parent != generated_root:
        raise ValueError("La imagen fuente queda fuera del almacenamiento permitido.")
    return resolved


def _usage_value(source: object | None, field: str) -> int | None:
    if source is None:
        return None
    value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
    return value if isinstance(value, int) and value >= 0 else None


def _worn_usage_cost(response: object) -> float:
    usage = getattr(response, "usage", None)
    details = (
        usage.get("input_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "input_tokens_details", None)
    )
    text_tokens = _usage_value(details, "text_tokens")
    image_tokens = _usage_value(details, "image_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    if None in (text_tokens, image_tokens, output_tokens):
        return WORN_VIEW_ESTIMATED_COST
    return estimate_gpt_image_2_token_cost(
        text_input_tokens=text_tokens,
        image_input_tokens=image_tokens,
        image_output_tokens=output_tokens,
    )


def generate_outfit_image(final_prompt: str) -> ImageDetails:
    """
    Genera la imagen del outfit utilizando OpenAI.
    Recibe el prompt YA compuesto (ver app/prompts/image_prompt_builder.py).
    Pide la imagen en base64, la guarda en storage propio (disco local) y devuelve
    una URL/ruta propia en 'url_or_base64'. No se depende de la URL efímera de OpenAI.
    """
    settings = get_settings()
    client = _get_client()
    model = settings.openai_image_model
    quality = settings.image_quality
    size = settings.image_size

    # Validar la tarifa ANTES de llamar a la API: si la combinación quality/size
    # no está verificada en pricing.py debe fallar aquí (sin haber pagado nada),
    # no en _persist_image con la imagen ya generada y cobrada.
    estimate_image_cost(quality, size)

    logger.info(f"Generando imagen con modelo {model}. Prompt: {final_prompt}")

    try:
        # Los modelos GPT Image (gpt-image-2) devuelven base64 en data[0].b64_json
        # SIEMPRE por defecto. NO aceptan el parámetro response_format (da error 400).
        response = client.images.generate(
            model=model,
            prompt=final_prompt,
            size=size,
            quality=quality,
            n=1,
        )
        image_ref = _save_image_b64(response.data[0].b64_json)
    except Exception as e:
        logger.error(f"Error generando la imagen: {e}")
        raise ImageGenerationError(str(e)) from e

    return ImageDetails(
        model=model,
        quality=quality,
        size=size,
        url_or_base64=image_ref,
    )


def generate_worn_view_image(final_prompt: str, reference_path: Path) -> WornImageGeneration:
    """Transforma una composición local en una vista puesta mediante una sola edición."""
    settings = get_settings()
    model = settings.openai_image_model
    quality = "low"
    size = "1024x1536"

    if not reference_path.is_file():
        raise ImageGenerationError("No existe la imagen flat-lay de referencia.")

    # Barrera de precio previa y cliente sin reintentos: cada nuevo intento debe
    # proceder de otra acción explícita del usuario.
    estimate_image_cost(quality, size)
    client = _get_client(max_retries=0)

    try:
        with reference_path.open("rb") as reference_file:
            response = client.images.edit(
                image=reference_file,
                model=model,
                prompt=final_prompt,
                size=size,
                quality=quality,
                n=1,
            )
        image_ref = _save_image_b64(response.data[0].b64_json)
        cost_estimate = _worn_usage_cost(response)
    except Exception as exc:
        logger.error("Error generando la vista puesta: %s", exc)
        raise ImageGenerationError(str(exc)) from exc

    return WornImageGeneration(
        image=ImageDetails(
            model=model,
            quality=quality,
            size=size,
            url_or_base64=image_ref,
        ),
        cost_estimate=cost_estimate,
    )
