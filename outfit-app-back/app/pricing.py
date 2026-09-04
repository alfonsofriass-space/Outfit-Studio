"""
Tarifas de OpenAI verificadas (jul 2026) para estimar el coste por imagen.
Ver pricing.md (raíz del backend) para el detalle y las fuentes.
Actualizar aquí si OpenAI cambia tarifas.
"""

# Coste por imagen de gpt-image-2, por tamaño y calidad (USD).
# Solo se listan combinaciones VERIFICADAS contra el pricing oficial.
# Los formatos alternativos siguen deshabilitados porque el producto usa 1024x1024.
# No estimar sus tarifas por área: el pricing oficial de imagen no es proporcional.
# Añadir una combinación solo cuando exista una decisión de producto y una tarifa
# oficial verificada.
IMAGE_COST = {
    "1024x1024": {
        "low": 0.006,
        "medium": 0.053,
        "high": 0.211,
    },
    # Formato fijo de la vista puesta validada; el board continúa limitado a
    # 1024x1024 en Settings. No se habilitan calidades portrait no verificadas.
    "1024x1536": {
        "low": 0.005,
    },
}

# Estimación previa mostrada al usuario y fallback si una edición completada no
# incluye usage. El A/B real calculó $0.014252 para la referencia 1024x1024.
WORN_VIEW_ESTIMATED_COST = 0.015

# Búsqueda textual validada con gpt-5.4-nano y web_search. Una petición puede
# completar una prenda con una o dos acciones web; el valor visible cubre ese
# máximo y el coste persistido se reconcilia después desde usage.
PRODUCT_SEARCH_ESTIMATED_COST = 0.03
PRODUCT_SEARCH_WEB_CALL_COST = 0.01
GPT_5_4_NANO_INPUT_COST_PER_MILLION = 0.20
GPT_5_4_NANO_OUTPUT_COST_PER_MILLION = 1.25
GPT_5_4_MINI_INPUT_COST_PER_MILLION = 0.75
GPT_5_4_MINI_OUTPUT_COST_PER_MILLION = 4.50

# Tarifas de texto por 1M de tokens, ya verificadas en pricing.md §"Modelos de texto".
# Solo se listan los modelos que el producto puede configurar: un modelo ausente
# tiene que fallar igual que una combinación de imagen no verificada.
TEXT_COST_PER_MILLION = {
    "gpt-5.4-nano": (
        GPT_5_4_NANO_INPUT_COST_PER_MILLION,
        GPT_5_4_NANO_OUTPUT_COST_PER_MILLION,
    ),
    "gpt-5.4-mini": (
        GPT_5_4_MINI_INPUT_COST_PER_MILLION,
        GPT_5_4_MINI_OUTPUT_COST_PER_MILLION,
    ),
}

# Tarifas estándar por 1M tokens de gpt-image-2. Permiten reconciliar el coste
# completo de una edición cuando la respuesta del proveedor incluye usage.
GPT_IMAGE_2_TEXT_INPUT_COST_PER_MILLION = 5.0
GPT_IMAGE_2_IMAGE_INPUT_COST_PER_MILLION = 8.0
GPT_IMAGE_2_IMAGE_OUTPUT_COST_PER_MILLION = 30.0


def estimate_image_cost(quality: str, size: str = "1024x1024") -> float:
    """
    Coste estimado de una generación de imagen. Falla explícitamente ante una
    combinación no verificada: registrar un coste inventado en BD es peor que
    parar, porque corrompe la medición de coste real (pricing.md §5).
    """
    try:
        return IMAGE_COST[size][quality]
    except KeyError:
        raise ValueError(
            f"Tarifa no verificada para size={size!r}, quality={quality!r}. "
            "Añadirla a IMAGE_COST (app/pricing.py) tras comprobar el pricing oficial."
        )


def estimate_gpt_image_2_token_cost(
    *,
    text_input_tokens: int = 0,
    image_input_tokens: int = 0,
    image_output_tokens: int = 0,
) -> float:
    """Calcula el coste estándar de uso reportado por gpt-image-2."""
    token_counts = (text_input_tokens, image_input_tokens, image_output_tokens)
    if any(count < 0 for count in token_counts):
        raise ValueError("Los recuentos de tokens no pueden ser negativos.")

    return (
        text_input_tokens * GPT_IMAGE_2_TEXT_INPUT_COST_PER_MILLION
        + image_input_tokens * GPT_IMAGE_2_IMAGE_INPUT_COST_PER_MILLION
        + image_output_tokens * GPT_IMAGE_2_IMAGE_OUTPUT_COST_PER_MILLION
    ) / 1_000_000


def estimate_product_search_cost(
    *,
    web_search_calls: int,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calcula el coste estándar reportable de una búsqueda de producto."""
    counts = (web_search_calls, input_tokens, output_tokens)
    if any(count < 0 for count in counts):
        raise ValueError("Los recuentos de uso no pueden ser negativos.")

    return (
        web_search_calls * PRODUCT_SEARCH_WEB_CALL_COST
        + input_tokens * GPT_5_4_NANO_INPUT_COST_PER_MILLION / 1_000_000
        + output_tokens * GPT_5_4_NANO_OUTPUT_COST_PER_MILLION / 1_000_000
    )


def estimate_text_cost(model: str, *, input_tokens: int, output_tokens: int) -> float:
    """
    Coste reportable de una llamada de texto a partir del usage devuelto.

    Falla ante un modelo sin tarifa verificada por la misma razón que
    `estimate_image_cost`: persistir un coste inventado corrompe la medición real.
    """
    counts = (input_tokens, output_tokens)
    if any(count < 0 for count in counts):
        raise ValueError("Los recuentos de tokens no pueden ser negativos.")

    try:
        input_cost, output_cost = TEXT_COST_PER_MILLION[model]
    except KeyError:
        raise ValueError(
            f"Tarifa de texto no verificada para model={model!r}. "
            "Añadirla a TEXT_COST_PER_MILLION (app/pricing.py) tras comprobar el "
            "pricing oficial."
        ) from None

    return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000
