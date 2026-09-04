import json
import logging
import re

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Outfit, ProductSearch, User
from app.pricing import PRODUCT_SEARCH_ESTIMATED_COST
from app.schemas import (
    OutfitExtraction,
    OutfitItem,
    ProductCandidate,
    ProductSearchDetails,
    ProductSearchItemState,
    ProductSearchRequest,
    ProductSearchResponse,
)
from app.services.openai_product_search import detect_known_brand, search_products
from app.services.outfit_operation_lease import (
    OutfitOperationInProgressError,
    acquire_outfit_operation_lease,
    delete_owned_outfit_operation_lease,
    release_outfit_operation_lease,
)

logger = logging.getLogger(__name__)

GENERIC_TYPES = {
    "accesorio",
    "accesorios",
    "complemento",
    "complementos",
    "prenda",
    "prendas",
    "ropa",
}

# Búsqueda original + 2 repeticiones. Cada intento es una llamada web pagada, así que
# el bucle se acota igual que MAX_REGENERATIONS acota las imágenes.
MAX_PRODUCT_SEARCH_ATTEMPTS = 3


class ProductSearchLimitError(ValueError):
    """La prenda agotó los intentos de búsqueda permitidos."""


class ProductSearchInputError(ValueError):
    """La prenda no contiene información suficiente para gastar en buscarla."""


class ProductSearchDataError(ValueError):
    """El outfit o una búsqueda persistida no contienen datos recuperables."""


def _clean(value: str | None) -> str | None:
    normalized = " ".join(value.split()) if value else ""
    return normalized or None


def _without_brand(value: str | None, brand: str | None) -> str | None:
    """Evita duplicar una marca detectada al construir la consulta."""
    normalized = _clean(value)
    if not normalized or not brand:
        return normalized
    without_brand = re.sub(
        rf"(?<!\w){re.escape(brand)}(?!\w)",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    return _clean(without_brand.strip(" ,;:/|-"))


def build_product_query(
    item: OutfitItem,
    additional_details: str | None = None,
    brand: str | None = None,
) -> str:
    """Usa solo atributos persistidos y texto adicional escrito por el usuario."""
    item_type = _clean(item.item_type)
    additional = _clean(additional_details)
    requested_brand = _clean(brand) or _clean(item.brand)
    if not item_type:
        raise ProductSearchInputError(
            "Esta prenda no tiene un tipo concreto. Añade qué prenda quieres buscar."
        )

    is_generic = item_type.casefold() in GENERIC_TYPES
    if is_generic and not additional:
        raise ProductSearchInputError(
            f"«{item.item_type}» es demasiado genérico. "
            "Añade el tipo de prenda, color, material, corte o detalle."
        )

    query_additional = additional if is_generic else _without_brand(additional, requested_brand)
    terms = [query_additional] if is_generic else [item_type]
    values = (requested_brand, item.color, item.material, item.fit, *item.details)
    if not is_generic:
        values = (*values, query_additional)
    for value in values:
        normalized = _clean(value)
        if normalized and not any(
            normalized.casefold() in term.casefold() for term in terms if term
        ):
            terms.append(normalized)

    concrete_terms = [term for term in terms if term]
    if len(concrete_terms) == 1 and not additional and not is_generic:
        raise ProductSearchInputError(
            f"«{item_type}» necesita color, material, corte o algún detalle antes de buscar."
        )
    return " ".join([*concrete_terms, "comprar online España"])


def _load_extraction(outfit: Outfit) -> OutfitExtraction:
    try:
        extraction = OutfitExtraction.model_validate_json(outfit.outfit_json)
    except (ValidationError, ValueError) as exc:
        raise ProductSearchDataError(
            "El análisis guardado no permite buscar sus prendas de forma segura."
        ) from exc
    if extraction.status != "ok" or not extraction.items:
        raise ProductSearchDataError(
            "El análisis guardado no contiene prendas disponibles para buscar."
        )
    return extraction


def _search_details(search: ProductSearch) -> ProductSearchDetails:
    try:
        raw_candidates = json.loads(search.candidates_json)
        candidates = [ProductCandidate.model_validate(candidate) for candidate in raw_candidates]
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise ProductSearchDataError(
            "Una búsqueda guardada contiene candidatos no recuperables."
        ) from exc

    return ProductSearchDetails(
        item_index=search.item_index,
        attempt=search.attempt,
        query=search.query,
        additional_details=search.additional_details,
        candidates=candidates,
        model=search.search_model,
        web_search_calls=search.web_search_calls,
        input_tokens=search.input_tokens,
        output_tokens=search.output_tokens,
        cost_estimate=search.cost_estimate,
        created_at=search.created_at,
    )


def build_product_search_item_states(
    extraction: OutfitExtraction,
    searches: list[ProductSearch],
) -> list[ProductSearchItemState]:
    """Proyecta consulta, coste, último intento y presupuesto sin llamar al proveedor."""
    attempts_by_index: dict[int, list[ProductSearch]] = {}
    for search in searches:
        attempts_by_index.setdefault(search.item_index, []).append(search)

    states: list[ProductSearchItemState] = []
    for item_index, item in enumerate(extraction.items):
        persisted = sorted(
            attempts_by_index.get(item_index, []),
            key=lambda search: search.attempt,
        )
        attempts = len(persisted)
        remaining = max(MAX_PRODUCT_SEARCH_ATTEMPTS - attempts, 0)
        if persisted:
            latest = persisted[-1]
            states.append(
                ProductSearchItemState(
                    item_index=item_index,
                    query=latest.query,
                    needs_details=False,
                    estimated_cost=PRODUCT_SEARCH_ESTIMATED_COST,
                    search=_search_details(latest),
                    attempts=attempts,
                    attempts_remaining=remaining,
                )
            )
            continue

        try:
            query = build_product_query(item)
        except ProductSearchInputError as exc:
            states.append(
                ProductSearchItemState(
                    item_index=item_index,
                    needs_details=True,
                    message=str(exc),
                    estimated_cost=PRODUCT_SEARCH_ESTIMATED_COST,
                    attempts_remaining=remaining,
                )
            )
        else:
            states.append(
                ProductSearchItemState(
                    item_index=item_index,
                    query=query,
                    needs_details=False,
                    estimated_cost=PRODUCT_SEARCH_ESTIMATED_COST,
                    attempts_remaining=remaining,
                )
            )
    return states


def _latest_attempt(db: Session, outfit_id: int, item_index: int) -> ProductSearch | None:
    """Devuelve el intento más reciente de esa prenda, o None si no hay ninguno."""
    return db.scalar(
        select(ProductSearch)
        .where(
            ProductSearch.outfit_id == outfit_id,
            ProductSearch.item_index == item_index,
        )
        .order_by(ProductSearch.attempt.desc())
        .limit(1)
    )


def _response(
    *,
    outfit_id: int,
    search: ProductSearch,
    created: bool,
) -> ProductSearchResponse:
    return ProductSearchResponse(
        created=created,
        outfit_id=outfit_id,
        item_index=search.item_index,
        search=_search_details(search),
    )


def search_persisted_outfit_item(
    outfit_id: int,
    item_index: int,
    request: ProductSearchRequest,
    db: Session,
    current_user: User | None = None,
) -> ProductSearchResponse | None:
    """Busca una prenda una sola vez y devuelve la caché en peticiones posteriores."""
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or (
        current_user is not None
        and current_user.role != "admin"
        and outfit.owner_id != current_user.id
    ):
        return None
    extraction = _load_extraction(outfit)
    if item_index < 0 or item_index >= len(extraction.items):
        return None

    latest = _latest_attempt(db, outfit_id, item_index)
    if latest is not None and not request.force_new:
        # Caché: una recarga, un doble clic o un reintento accidental no pagan.
        return _response(outfit_id=outfit_id, search=latest, created=False)
    if latest is not None and latest.attempt >= MAX_PRODUCT_SEARCH_ATTEMPTS:
        raise ProductSearchLimitError(
            f"Esta prenda ya ha agotado sus {MAX_PRODUCT_SEARCH_ATTEMPTS} búsquedas."
        )

    item = extraction.items[item_index]
    requested_brand = (
        detect_known_brand(request.additional_details)
        or _clean(item.brand)
        or detect_known_brand(item.item_type, *item.details, item.visual_phrase_en)
    )
    query = build_product_query(
        item,
        request.additional_details,
        requested_brand,
    )
    lease_token = acquire_outfit_operation_lease(db, outfit_id)
    lease_closed = False
    try:
        # Otra petición pudo añadir un intento entre el atajo y la reserva.
        current = _latest_attempt(db, outfit_id, item_index)
        if current is not None and not request.force_new:
            if not delete_owned_outfit_operation_lease(db, outfit_id, lease_token):
                raise OutfitOperationInProgressError(
                    "La reserva dejó de pertenecer a esta petición."
                )
            db.commit()
            lease_closed = True
            return _response(outfit_id=outfit_id, search=current, created=False)

        attempts_done = current.attempt if current is not None else 0
        if attempts_done >= MAX_PRODUCT_SEARCH_ATTEMPTS:
            if not delete_owned_outfit_operation_lease(db, outfit_id, lease_token):
                raise OutfitOperationInProgressError(
                    "La reserva dejó de pertenecer a esta petición."
                )
            db.commit()
            lease_closed = True
            raise ProductSearchLimitError(
                f"Esta prenda ya ha agotado sus {MAX_PRODUCT_SEARCH_ATTEMPTS} búsquedas."
            )

        # La fila de reserva ya está confirmada; no queda una transacción de
        # escritura abierta durante la llamada web.
        provider_result = search_products(query, brand=requested_brand)
        persisted = ProductSearch(
            outfit_id=outfit_id,
            item_index=item_index,
            attempt=attempts_done + 1,
            query=query,
            additional_details=request.additional_details,
            candidates_json=json.dumps(
                [candidate.model_dump(mode="json") for candidate in provider_result.candidates],
                ensure_ascii=False,
            ),
            search_model=provider_result.model,
            web_search_calls=provider_result.web_search_calls,
            input_tokens=provider_result.input_tokens,
            output_tokens=provider_result.output_tokens,
            cost_estimate=provider_result.cost_estimate,
        )
        db.add(persisted)
        db.flush()
        if not delete_owned_outfit_operation_lease(db, outfit_id, lease_token):
            db.rollback()
            raise OutfitOperationInProgressError("La reserva caducó antes de guardar la búsqueda.")
        db.commit()
        lease_closed = True
        return _response(outfit_id=outfit_id, search=persisted, created=True)
    finally:
        if not lease_closed:
            try:
                release_outfit_operation_lease(db, outfit_id, lease_token)
            except Exception:
                logger.exception(
                    "No se pudo liberar la reserva de búsqueda del outfit %s.",
                    outfit_id,
                )
