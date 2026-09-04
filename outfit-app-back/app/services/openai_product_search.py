import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.pricing import PRODUCT_SEARCH_ESTIMATED_COST, estimate_product_search_cost
from app.schemas import ProductCandidate

logger = logging.getLogger(__name__)

MAX_RESULTS = 3
MAX_OUTPUT_TOKENS = 4_000
MAX_WEB_SEARCH_CALLS = 2

# Primera selección deliberadamente pequeña y conocida. No hay scraping propio:
# web_search solo puede devolver páginas de estos dominios para el mercado ES.
STORES = {
    "zara.com": "Zara",
    "mango.com": "Mango",
    "hm.com": "H&M",
    "uniqlo.com": "Uniqlo",
    "massimodutti.com": "Massimo Dutti",
    "pullandbear.com": "Pull&Bear",
    "bershka.com": "Bershka",
    "stradivarius.com": "Stradivarius",
    "zalando.es": "Zalando",
    "elcorteingles.es": "El Corte Inglés",
}

# Las tiendas de marca se excluyen cuando el usuario pide otra marca concreta:
# no tiene sentido sustituir Versace por Zara solo por coincidir en color. Los
# distribuidores multimarca siguen disponibles y el dominio oficial reconocido
# se añade al principio de la allowlist.
MULTIBRAND_STORES = {
    "zalando.es": STORES["zalando.es"],
    "elcorteingles.es": STORES["elcorteingles.es"],
}
OFFICIAL_BRAND_STORES = {
    "versace": ("versace.com", "Versace"),
    "versace jeans couture": ("versace.com", "Versace"),
}
KNOWN_BRAND_ALIASES = {
    "versace jeans couture": "Versace Jeans Couture",
    "massimo dutti": "Massimo Dutti",
    "pull bear": "Pull&Bear",
    "stradivarius": "Stradivarius",
    "bershka": "Bershka",
    "versace": "Versace",
    "uniqlo": "Uniqlo",
    "mango": "Mango",
    "zara": "Zara",
    "h m": "H&M",
}


class ProductSearchProviderError(Exception):
    """OpenAI rechazó la petición o devolvió un resultado no verificable."""


class ProductSearchIncompleteError(ProductSearchProviderError):
    """La respuesta terminó antes de producir el resultado estructurado completo."""


class ProductSearchServiceUnavailableError(ProductSearchProviderError):
    """La cuenta, conexión o servicio no permiten completar la búsqueda."""


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    product_url: str
    price_text: str | None


class _SearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[_Candidate] = Field(max_length=MAX_RESULTS)


@dataclass(frozen=True)
class ProductSearchProviderResult:
    candidates: list[ProductCandidate]
    model: str
    web_search_calls: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


_SERVICE_UNAVAILABLE_ERRORS = (
    APIConnectionError,  # Incluye APITimeoutError.
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    InternalServerError,
)


def _clean(value: str | None) -> str | None:
    normalized = " ".join(value.split()) if value else ""
    return normalized or None


def _searchable_text(value: str) -> str:
    ascii_value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))


def detect_known_brand(*values: str | None) -> str | None:
    """Detecta una marca soportada cuando aparece explícitamente en el texto."""
    searchable = _searchable_text(" ".join(value for value in values if value))
    padded = f" {searchable} "
    for alias in sorted(KNOWN_BRAND_ALIASES, key=len, reverse=True):
        if f" {alias} " in padded:
            return KNOWN_BRAND_ALIASES[alias]
    return None


def _official_store_for_brand(brand: str | None) -> tuple[str, str] | None:
    if not brand:
        return None
    brand_key = _searchable_text(brand)
    configured = OFFICIAL_BRAND_STORES.get(brand_key)
    if configured:
        return configured
    return next(
        ((domain, name) for domain, name in STORES.items() if _searchable_text(name) == brand_key),
        None,
    )


def _stores_for_brand(brand: str | None) -> dict[str, str]:
    if not brand:
        return dict(STORES)

    stores: dict[str, str] = {}
    official_store = _official_store_for_brand(brand)
    if official_store:
        domain, name = official_store
        stores[domain] = name
    for domain, name in MULTIBRAND_STORES.items():
        stores.setdefault(domain, name)
    return stores


def _incomplete_reason(data: dict[str, Any]) -> str | None:
    details = data.get("incomplete_details")
    if not isinstance(details, dict):
        return None
    reason = details.get("reason")
    return _clean(reason) if isinstance(reason, str) else None


def _ensure_completed_response(data: dict[str, Any]) -> None:
    status = data.get("status")
    # Los dobles de prueba antiguos pueden no incluir status; una respuesta real
    # de Responses siempre lo incluye.
    if status is None or status == "completed":
        return
    if status == "incomplete":
        reason = _incomplete_reason(data)
        suffix = f" ({reason})" if reason else ""
        raise ProductSearchIncompleteError(
            f"La búsqueda devolvió una respuesta incompleta{suffix}."
        )
    raise ProductSearchProviderError(f"La búsqueda terminó con un estado inesperado: {status!r}.")


def _build_prompt(query: str, brand: str | None = None) -> str:
    instructions = [
        "Busca páginas de producto concretas para esta prenda.",
        f"Consulta exacta: {query}",
        "Mercado: España. Usa exclusivamente los dominios permitidos.",
        "Mantén el tipo de prenda como requisito y usa todos los términos "
        "descriptivos de la consulta.",
    ]
    official_store = _official_store_for_brand(brand)
    if brand:
        instructions.extend(
            [
                f"Marca obligatoria: {brand}.",
                "Todos los candidatos deben pertenecer inequívocamente a esa marca. "
                "No devuelvas productos de otra marca aunque coincidan mejor en color.",
                "Conserva primero tipo y marca; después ordena por coincidencia con color, "
                "material, corte y detalles. Si no existe el color exacto, devuelve la "
                "prenda más cercana de la misma marca.",
                "Incluye la marca en el título de cada candidato procedente de un "
                "distribuidor multimarca.",
            ]
        )
        if official_store:
            instructions.append(
                f"Busca primero en la tienda oficial {official_store[0]} y usa después "
                "los distribuidores multimarca permitidos."
            )
        instructions.append(
            "Si no encuentras ningún producto verificable de esa marca, devuelve "
            "candidates vacío; no busques sustitutos de otras marcas."
        )
    else:
        instructions.extend(
            [
                "Si la consulta nombra explícitamente una marca, consérvala como requisito "
                "y no la sustituyas por otra.",
                "Ordena los candidatos por coincidencia con color, material, corte y detalles. "
                "Si no existe una coincidencia exacta, conserva el tipo de prenda y prioriza "
                "las alternativas que mantengan más atributos explícitos.",
            ]
        )
    instructions.extend(
        [
            "Usa una sola acción web cuando sea suficiente y nunca más de dos.",
            f"Devuelve como máximo {MAX_RESULTS} candidatos.",
            "Excluye categorías, editoriales, inspiración, segunda mano y agregadores.",
            "No inventes URL, precio, disponibilidad, material ni detalles.",
            "Devuelve solo lo encontrado y no calcules porcentajes de similitud.",
        ]
    )
    return "\n".join(instructions)


def _web_search_tool(stores: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "web_search",
        "search_context_size": "low",
        "external_web_access": True,
        "filters": {"allowed_domains": list(stores)},
        "user_location": {"type": "approximate", "country": "ES"},
    }


def _request_payload(
    query: str,
    model: str,
    brand: str | None = None,
) -> dict[str, Any]:
    stores = _stores_for_brand(brand)
    return {
        "model": model,
        "reasoning": {"effort": "low"},
        "input": _build_prompt(query, brand),
        "tools": [_web_search_tool(stores)],
        "tool_choice": "required",
        "max_tool_calls": MAX_WEB_SEARCH_CALLS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "parallel_tool_calls": False,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "product_search_results",
                "strict": True,
                "schema": _SearchOutput.model_json_schema(),
            }
        },
        "store": False,
    }


def _get_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_timeout_text,
        # Una repetición puede cobrar una segunda búsqueda. Todo nuevo intento
        # debe proceder de otra acción explícita del usuario.
        max_retries=0,
    )


def _response_data(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    try:
        data = response.model_dump(mode="json")
    except AttributeError as exc:
        raise ProductSearchProviderError(
            "OpenAI devolvió un tipo de respuesta inesperado."
        ) from exc
    if not isinstance(data, dict):
        raise ProductSearchProviderError("OpenAI devolvió un tipo de respuesta inesperado.")
    return data


def _response_text(response: object, data: dict[str, Any]) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if content.get("type") == "output_text":
                    return content["text"]
    raise ProductSearchProviderError("La búsqueda no devolvió resultados estructurados.")


def _web_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    return value.strip() if parsed.scheme in {"http", "https"} and parsed.hostname else None


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _store(url: str, stores: dict[str, str]) -> str | None:
    host = (urlsplit(url).hostname or "").casefold()
    return next(
        (name for domain, name in stores.items() if host == domain or host.endswith(f".{domain}")),
        None,
    )


def _matches_brand(
    title: str,
    source_url: str,
    brand: str | None,
    official_domain: str | None,
) -> bool:
    if not brand:
        return True

    host = (urlsplit(source_url).hostname or "").casefold()
    if official_domain and (host == official_domain or host.endswith(f".{official_domain}")):
        return True

    brand_phrase = _searchable_text(brand)
    candidate_text = _searchable_text(f"{title} {urlsplit(source_url).path}")
    return f" {brand_phrase} " in f" {candidate_text} "


def _search_sources(data: dict[str, Any]) -> tuple[int, dict[str, str]]:
    """Reúne las URL que el proveedor declara haber visitado.

    Estas fuentes son la verja anti-alucinación: solo se acepta un candidato cuya
    URL aparezca aquí. No se leen las entradas de ``web_search_call.results``
    porque ampliarían el criterio de aceptación sin necesidad demostrada.
    """
    calls = 0
    sources: dict[str, str] = {}

    def add_source(value: object) -> None:
        url = _web_url(value)
        if url:
            sources.setdefault(_url_key(url), url)

    for item in data.get("output") or []:
        if item.get("type") == "web_search_call":
            calls += 1
            for source in (item.get("action") or {}).get("sources") or []:
                add_source(source.get("url"))
        elif item.get("type") == "message":
            for content in item.get("content") or []:
                for annotation in content.get("annotations") or []:
                    add_source(annotation.get("url"))
    return calls, sources


def _validated_candidates(
    candidates: Iterable[_Candidate],
    sources: dict[str, str],
    stores: dict[str, str],
    brand: str | None = None,
) -> list[ProductCandidate]:
    matches: list[ProductCandidate] = []
    seen: set[str] = set()
    official_store = _official_store_for_brand(brand)
    official_domain = official_store[0] if official_store else None
    for candidate in candidates:
        title = _clean(candidate.title)
        candidate_url = _web_url(candidate.product_url)
        key = _url_key(candidate_url) if candidate_url else ""
        source_url = sources.get(key)
        store = _store(source_url, stores) if source_url else None
        if (
            not title
            or not source_url
            or not store
            or key in seen
            or not _matches_brand(title, source_url, brand, official_domain)
        ):
            continue
        seen.add(key)
        matches.append(
            ProductCandidate(
                title=title,
                store=store,
                product_url=source_url,
                price_text=_clean(candidate.price_text),
            )
        )
    return matches


def _usage(data: dict[str, Any]) -> tuple[int, int] | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    if input_tokens < 0 or output_tokens < 0:
        return None
    return input_tokens, output_tokens


def _log_unusable_response(
    data: dict[str, Any],
    error: ProductSearchProviderError,
) -> None:
    output = data.get("output")
    output_types = (
        [item.get("type") if isinstance(item, dict) else type(item).__name__ for item in output]
        if isinstance(output, list)
        else [type(output).__name__]
    )
    logger.warning(
        "Respuesta de búsqueda no utilizable "
        "(response_id=%r, status=%r, incomplete_reason=%r, "
        "output_types=%s, usage=%r, error=%s).",
        data.get("id"),
        data.get("status"),
        _incomplete_reason(data),
        output_types,
        _usage(data),
        type(error).__name__,
    )


def search_products(query: str, brand: str | None = None) -> ProductSearchProviderResult:
    """Ejecuta una única búsqueda web y conserva solo candidatos verificables."""
    settings = get_settings()
    model = settings.openai_product_search_model
    requested_brand = _clean(brand)
    stores = _stores_for_brand(requested_brand)
    client = _get_client()
    try:
        response = client.responses.create(**_request_payload(query, model, requested_brand))
    except _SERVICE_UNAVAILABLE_ERRORS as exc:
        logger.error("La búsqueda de producto no está disponible (%s).", type(exc).__name__)
        raise ProductSearchServiceUnavailableError(
            "El servicio de búsqueda de productos no está disponible."
        ) from exc
    except OpenAIError as exc:
        logger.error("OpenAI rechazó la búsqueda de producto (%s).", type(exc).__name__)
        raise ProductSearchProviderError(
            "OpenAI no pudo completar la búsqueda de productos."
        ) from exc

    try:
        data = _response_data(response)
    except ProductSearchProviderError:
        logger.warning(
            "La búsqueda devolvió un objeto de respuesta no serializable (%s).",
            type(response).__name__,
        )
        raise

    try:
        _ensure_completed_response(data)
        try:
            calls, sources = _search_sources(data)
            response_text = _response_text(response, data)
        except (AttributeError, KeyError, TypeError) as exc:
            raise ProductSearchProviderError(
                "La búsqueda devolvió una estructura de fuentes inesperada."
            ) from exc
        if not 1 <= calls <= MAX_WEB_SEARCH_CALLS:
            raise ProductSearchProviderError(
                f"Se esperaban entre 1 y {MAX_WEB_SEARCH_CALLS} acciones web; "
                f"se observaron {calls}."
            )

        try:
            parsed = _SearchOutput.model_validate_json(response_text)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProductSearchProviderError(
                "La búsqueda no devolvió productos estructurados válidos."
            ) from exc
    except ProductSearchProviderError as exc:
        _log_unusable_response(data, exc)
        raise

    usage = _usage(data)
    if usage is None:
        input_tokens = output_tokens = 0
        cost_estimate = PRODUCT_SEARCH_ESTIMATED_COST
    else:
        input_tokens, output_tokens = usage
        cost_estimate = estimate_product_search_cost(
            web_search_calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return ProductSearchProviderResult(
        candidates=_validated_candidates(
            parsed.candidates,
            sources,
            stores,
            requested_brand,
        ),
        model=model,
        web_search_calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=round(cost_estimate, 6),
    )
