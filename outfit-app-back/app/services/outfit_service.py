import logging

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import Image, Outfit, User, WornView
from app.pricing import WORN_VIEW_ESTIMATED_COST, estimate_image_cost
from app.prompts.image_prompt_builder import build_image_prompt
from app.prompts.worn_prompt_builder import build_worn_view_prompt
from app.schemas import (
    ClarificationResponse,
    FinalOutfitResponse,
    ImageDetails,
    ModelsUsed,
    OutfitDetailResponse,
    OutfitExtraction,
    OutfitImageResponse,
    OutfitRequest,
    OutfitUpdateRequest,
    RegenerationLimitResponse,
    RegenerationResponse,
    WornViewDetails,
    WornViewPreview,
    WornViewResponse,
)
from app.services.openai_image import (
    ImageGenerationError,
    generate_outfit_image,
    generate_worn_view_image,
    resolve_generated_image_path,
)
from app.services.openai_text import extract_outfit_from_text
from app.services.outfit_operation_lease import (
    OUTFIT_OPERATION_LEASE_TTL,
    OutfitOperationInProgressError,
    acquire_outfit_operation_lease,
    delete_owned_outfit_operation_lease,
    release_outfit_operation_lease,
)
from app.services.product_search_service import build_product_search_item_states
from app.validation import (
    CLARIFICATION_SUGGESTION,
    clarification_for,
    evaluate_minimum_info,
    is_outfit_valid,
)

logger = logging.getLogger(__name__)

# Límite: 1 imagen original + 3 regeneraciones = 4 imágenes como máximo por outfit.
MAX_REGENERATIONS = 3

# Alias conservados para no romper el contrato interno y sus pruebas históricas.
# La misma reserva serializa ahora todas las operaciones pagadas y el borrado.
REGENERATION_LEASE_TTL = OUTFIT_OPERATION_LEASE_TTL
RegenerationInProgressError = OutfitOperationInProgressError
_acquire_regeneration_lease = acquire_outfit_operation_lease
_delete_owned_regeneration_lease = delete_owned_outfit_operation_lease
_release_regeneration_lease = release_outfit_operation_lease

# Al regenerar con el MISMO prompt, el modelo tiende a repetir el mismo fallo de
# composición. Esta directiva pide otra disposición sin tocar las prendas.
VARIATION_DIRECTIVE = (
    "\nAlternative composition of the exact same garments; do not add or remove any piece."
)


class OutfitImagePromptMissingError(Exception):
    """El outfit no contiene un prompt visual seguro para generar una imagen."""


class OutfitImageNotInOutfitError(Exception):
    """La composición elegida como portada no pertenece a ese outfit."""


class WornViewUnavailableError(Exception):
    """La vista no puede construirse sin inventar o sin una referencia local válida."""


def _can_access_outfit(outfit: Outfit, current_user: User | None) -> bool:
    return (
        current_user is None or current_user.role == "admin" or outfit.owner_id == current_user.id
    )


def _persist_image(
    db: Session,
    outfit_id: int,
    image: ImageDetails,
    generation_prompt: str,
) -> Image:
    """Guarda una fila en 'images' con su coste estimado. Usado en generación y regeneración."""
    persisted = Image(
        outfit_id=outfit_id,
        path=image.url_or_base64,
        generation_prompt=generation_prompt,
        image_model=image.model,
        quality=image.quality,
        size=image.size,
        cost_estimate=estimate_image_cost(image.quality, image.size),
    )
    db.add(persisted)
    db.flush()
    return persisted


def _worn_view_preview(extraction: OutfitExtraction) -> WornViewPreview:
    return WornViewPreview(
        generation_prompt=build_worn_view_prompt(extraction),
        estimated_cost=WORN_VIEW_ESTIMATED_COST,
    )


def _flat_lay_estimated_cost() -> float:
    settings = get_settings()
    return estimate_image_cost(settings.image_quality, settings.image_size)


def _load_persisted_extraction(outfit: Outfit) -> OutfitExtraction:
    try:
        extraction = OutfitExtraction.model_validate_json(outfit.outfit_json)
    except (ValidationError, ValueError) as exc:
        raise WornViewUnavailableError(
            "El análisis guardado no permite construir una vista puesta segura."
        ) from exc
    if extraction.status != "ok" or not extraction.items:
        raise WornViewUnavailableError(
            "El análisis guardado no contiene prendas suficientes para una vista puesta."
        )
    return extraction


def _worn_view_details(worn_view: WornView) -> WornViewDetails:
    return WornViewDetails(
        worn_view_id=worn_view.id,
        source_image_id=worn_view.source_image_id,
        generation_prompt=worn_view.generation_prompt,
        image=ImageDetails(
            model=worn_view.image_model,
            quality=worn_view.quality,
            size=worn_view.size,
            url_or_base64=worn_view.path,
        ),
        cost_estimate=worn_view.cost_estimate,
        created_at=worn_view.created_at,
    )


def _worn_view_response(
    *,
    outfit_id: int,
    worn_view: WornView,
    created: bool,
) -> WornViewResponse:
    return WornViewResponse(
        created=created,
        outfit_id=outfit_id,
        source_image_id=worn_view.source_image_id,
        worn_view=_worn_view_details(worn_view),
    )


def _outfit_image_response(image: Image, generation_number: int) -> OutfitImageResponse:
    return OutfitImageResponse(
        image_id=image.id,
        generation_number=generation_number,
        generation_prompt=image.generation_prompt,
        image=ImageDetails(
            model=image.image_model,
            quality=image.quality,
            size=image.size,
            url_or_base64=image.path,
        ),
        cost_estimate=image.cost_estimate,
        created_at=image.created_at,
        worn_view=(_worn_view_details(image.worn_view) if image.worn_view is not None else None),
    )


def _outfit_detail_response(outfit: Outfit) -> OutfitDetailResponse:
    extraction = _load_persisted_extraction(outfit)
    built = build_image_prompt(extraction.items, extraction.styling_notes_en)
    images = sorted(outfit.images, key=lambda image: (image.created_at, image.id))
    regeneration_count = max(len(images) - 1, 0)
    chosen_image_id = next((image.id for image in images if image.is_chosen), None)

    return OutfitDetailResponse(
        outfit_id=outfit.id,
        user_description=outfit.user_description,
        outfit=extraction,
        image_prompt=outfit.image_prompt,
        text_model=outfit.text_model,
        flat_lay_estimated_cost=_flat_lay_estimated_cost(),
        accessories_omitted=built.accessories_omitted,
        worn_view_preview=(_worn_view_preview(extraction) if images else None),
        product_search_items=build_product_search_item_states(
            extraction,
            outfit.product_searches,
        ),
        images=[
            _outfit_image_response(image, generation_number)
            for generation_number, image in enumerate(images, start=1)
        ],
        chosen_image_id=chosen_image_id,
        is_favourite=outfit.is_favourite,
        regeneration_count=regeneration_count,
        regenerations_remaining=max(MAX_REGENERATIONS - regeneration_count, 0),
        created_at=outfit.created_at,
    )


def get_persisted_outfit(
    outfit_id: int,
    db: Session,
    current_user: User | None = None,
) -> OutfitDetailResponse | None:
    """Recupera un outfit completo sin repetir ninguna llamada a modelos."""
    outfit = db.scalar(
        select(Outfit)
        .options(
            selectinload(Outfit.images).selectinload(Image.worn_view),
            selectinload(Outfit.product_searches),
        )
        .where(Outfit.id == outfit_id)
    )
    if outfit is None or not _can_access_outfit(outfit, current_user):
        return None
    return _outfit_detail_response(outfit)


def list_persisted_outfits(
    db: Session,
    limit: int = 24,
    current_user: User | None = None,
    offset: int = 0,
    favourites_only: bool = False,
) -> list[OutfitDetailResponse]:
    """Lista outfits recientes, incluidos los análisis que aún no tienen imagen.

    El orden incluye el `id` como desempate para que paginar con `offset` no devuelva
    huecos ni repetidos cuando dos outfits comparten `created_at`.
    """
    statement = (
        select(Outfit)
        .options(
            selectinload(Outfit.images).selectinload(Image.worn_view),
            selectinload(Outfit.product_searches),
        )
        .order_by(Outfit.created_at.desc(), Outfit.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if current_user is not None and current_user.role != "admin":
        statement = statement.where(Outfit.owner_id == current_user.id)
    # El filtro va en la consulta y no en el cliente: filtrar después de paginar solo
    # miraría la página cargada y volvería a esconder outfits en silencio.
    if favourites_only:
        statement = statement.where(Outfit.is_favourite.is_(True))
    outfits = db.scalars(statement).all()
    return [_outfit_detail_response(outfit) for outfit in outfits]


def _remove_managed_image_files(public_paths: set[str], outfit_id: int) -> None:
    """Retira solo archivos referenciados que pertenezcan al almacenamiento local."""
    for public_path in public_paths:
        try:
            image_path = resolve_generated_image_path(public_path)
        except ValueError:
            logger.warning(
                "Se omitió una ruta no gestionada al borrar el outfit %s.",
                outfit_id,
            )
            continue

        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            # La BD ya se ha eliminado. Dejamos el PNG huérfano registrado en el log;
            # borrarlo antes del commit podría dejar filas apuntando a una ruta ausente.
            logger.exception(
                "No se pudo retirar un PNG local del outfit %s.",
                outfit_id,
            )


def update_persisted_outfit(
    outfit_id: int,
    request: OutfitUpdateRequest,
    db: Session,
    current_user: User | None = None,
) -> OutfitDetailResponse | None:
    """Cambia portada elegida y favorito. No hay llamada al proveedor ni coste.

    No usa la reserva por outfit: no compite con una operación pagada porque no
    escribe nada que una generación pueda estar leyendo.
    """
    outfit = db.scalar(
        select(Outfit)
        .options(
            selectinload(Outfit.images).selectinload(Image.worn_view),
            selectinload(Outfit.product_searches),
        )
        .where(Outfit.id == outfit_id)
    )
    if outfit is None or not _can_access_outfit(outfit, current_user):
        return None

    changed = request.model_fields_set
    if "chosen_image_id" in changed:
        target = request.chosen_image_id
        if target is not None and all(image.id != target for image in outfit.images):
            raise OutfitImageNotInOutfitError(
                f"La composición {target} no pertenece al outfit {outfit_id}."
            )
        # Se limpia y se vacía la sesión antes de marcar la nueva: el índice único
        # parcial rechazaría dos elegidas a la vez si las escrituras se ordenasen al
        # revés dentro del mismo flush.
        for image in outfit.images:
            image.is_chosen = False
        db.flush()
        if target is not None:
            for image in outfit.images:
                if image.id == target:
                    image.is_chosen = True
        db.flush()

    if "is_favourite" in changed:
        outfit.is_favourite = bool(request.is_favourite)

    db.commit()
    db.refresh(outfit)
    return _outfit_detail_response(outfit)


def delete_persisted_outfit(
    outfit_id: int,
    db: Session,
    current_user: User | None = None,
) -> bool:
    """Elimina un outfit y sus PNG sin competir con una generación activa."""
    initial_outfit = db.get(Outfit, outfit_id)
    if initial_outfit is None or not _can_access_outfit(initial_outfit, current_user):
        return False

    lease_token = _acquire_regeneration_lease(db, outfit_id)
    lease_closed = False
    public_paths: set[str] = set()
    try:
        outfit = db.scalar(
            select(Outfit)
            .options(selectinload(Outfit.images).selectinload(Image.worn_view))
            .where(Outfit.id == outfit_id)
        )
        if outfit is None or not _can_access_outfit(outfit, current_user):
            _delete_owned_regeneration_lease(db, outfit_id, lease_token)
            db.commit()
            lease_closed = True
            return False

        for image in outfit.images:
            public_paths.add(image.path)
            if image.worn_view is not None:
                public_paths.add(image.worn_view.path)

        db.delete(outfit)
        db.commit()
        # El ON DELETE CASCADE del outfit elimina también esta reserva.
        lease_closed = True
    finally:
        if not lease_closed:
            try:
                _release_regeneration_lease(db, outfit_id, lease_token)
            except Exception:
                logger.exception(
                    "No se pudo liberar la reserva tras borrar el outfit %s.",
                    outfit_id,
                )

    _remove_managed_image_files(public_paths, outfit_id)
    return True


def _replaceable_outfit(
    outfit_id: int | None,
    db: Session,
    current_user: User | None,
) -> Outfit | None:
    """Devuelve el análisis que puede reescribirse, o None para crear uno nuevo.

    Solo se reutiliza un análisis accesible que todavía no tiene composiciones ni
    búsquedas: reescribir `outfit_json` invalidaría los `item_index` de una búsqueda
    guardada y borraría el contexto de una imagen ya pagada. Ante cualquier duda se
    crea una fila nueva, que nunca destruye nada.
    """
    if outfit_id is None:
        return None

    outfit = db.scalar(
        select(Outfit)
        .options(selectinload(Outfit.images), selectinload(Outfit.product_searches))
        .where(Outfit.id == outfit_id)
    )
    if outfit is None or not _can_access_outfit(outfit, current_user):
        return None
    if outfit.images or outfit.product_searches:
        return None
    return outfit


def process_outfit_request(
    request: OutfitRequest,
    db: Session,
    current_user: User | None = None,
) -> FinalOutfitResponse | ClarificationResponse:
    # 1. Filtro de ruido previo (sin IA): solo descarta el texto sin señal de ropa.
    minimum_info = evaluate_minimum_info(request.user_description)
    if not minimum_info.is_sufficient:
        logger.info(
            "Texto sin señal de ropa (%s); no se llama a la IA.",
            minimum_info.reason.value,
        )
        message, suggestion = clarification_for(minimum_info)
        # Un texto sin ninguna señal de ropa suele ser una situación («boda en
        # octubre»), no un outfit mal escrito. En vez de un error seco, la respuesta
        # señala la vía que sí sabe leerlo. El cliente decide si la ofrece.
        return ClarificationResponse(
            message=message,
            suggestion=suggestion,
            suggested_mode="inspiration",
        )

    logger.debug(
        "Heurística previa: %s (prendas reconocidas=%d, solo accesorios=%s).",
        minimum_info.reason.value,
        minimum_info.recognized_items,
        minimum_info.only_accessories,
    )

    # 2. Extraer JSON estructurado con el LLM de texto
    extraction, used_primary, used_fallback = extract_outfit_from_text(request.user_description)

    # 3. Validar que cumple con el umbral mínimo
    is_valid, error_msg = is_outfit_valid(extraction)
    if not is_valid:
        logger.info(f"Outfit no válido, pidiendo aclaración: {error_msg}")
        return ClarificationResponse(message=error_msg, suggestion=CLARIFICATION_SUGGESTION)

    # 4. Componer el prompt de imagen en código (layout determinista por categorías).
    #    Se persiste en el outfit para reutilizarlo tal cual al regenerar.
    built = build_image_prompt(extraction.items, extraction.styling_notes_en)

    # 5. Generar la imagen ANTES de tocar la BD. La llamada tarda 16-43 s y un
    #    INSERT pendiente retendría el lock de escritura de SQLite todo ese tiempo
    #    (dos peticiones concurrentes → "database is locked"). No necesita
    #    outfit.id, así que la transacción posterior dura milisegundos.
    #    Si la generación falla, el outfit se guarda igualmente pero SIN fila de
    #    imagen: ni path falso, ni coste fantasma, ni regeneración consumida.
    image_details = None
    image_model_used = None
    image_error = None
    if request.generate_image:
        try:
            image_details = generate_outfit_image(built.prompt)
            image_model_used = image_details.model
        except ImageGenerationError as e:
            logger.error(f"Imagen no generada para '{request.user_description[:60]}': {e}")
            image_error = "La imagen no pudo generarse. Puedes regenerarla con el outfit guardado."

    # 6. Persistir outfit + imagen original (si la hay) en una transacción corta.
    #    Al editar una descripción se reutiliza el análisis anterior en vez de dejarlo
    #    huérfano; `_replaceable_outfit` garantiza que no destruye trabajo pagado.
    outfit = _replaceable_outfit(request.replace_outfit_id, db, current_user)
    if outfit is None:
        outfit = Outfit(owner_id=current_user.id if current_user is not None else None)
        db.add(outfit)
    outfit.user_description = request.user_description
    outfit.outfit_json = extraction.model_dump_json()
    outfit.image_prompt = built.prompt
    outfit.text_model = used_fallback or used_primary
    db.flush()  # asigna outfit.id
    persisted_image = None
    if image_details is not None:
        persisted_image = _persist_image(db, outfit.id, image_details, built.prompt)
    db.commit()

    # 7. Construir respuesta final
    models = ModelsUsed(
        text_primary=used_primary,
        text_fallback=used_fallback,
        image=image_model_used,
    )
    return FinalOutfitResponse(
        outfit_id=outfit.id,
        user_description=request.user_description,
        outfit=extraction,
        image=image_details,
        image_id=persisted_image.id if persisted_image is not None else None,
        image_error=image_error,
        image_prompt=built.prompt,
        flat_lay_estimated_cost=_flat_lay_estimated_cost(),
        accessories_omitted=built.accessories_omitted,
        worn_view_preview=(_worn_view_preview(extraction) if persisted_image is not None else None),
        # Un outfit recién creado no tiene búsquedas: los estados son las consultas
        # base y el presupuesto completo de intentos.
        product_search_items=build_product_search_item_states(extraction, []),
        models_used=models,
    )


def regenerate_outfit_image(
    outfit_id: int,
    db: Session,
    current_user: User | None = None,
) -> RegenerationResponse | RegenerationLimitResponse | None:
    """
    Regenera la imagen de un outfit existente. Reutiliza el image_prompt guardado
    y solo vuelve a llamar a gpt-image-2 (NO se llama al modelo de texto otra vez).
    Devuelve None si el outfit no existe. Aplica el límite de MAX_REGENERATIONS.
    Si la generación falla, ImageGenerationError se propaga SIN persistir nada:
    un intento fallido no consume el límite de regeneraciones (main.py → 502).
    Solo admite una generación activa por outfit; una segunda petición simultánea
    termina con RegenerationInProgressError (main.py → 409) antes de llamar a OpenAI.
    """
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or not _can_access_outfit(outfit, current_user):
        return None
    if not outfit.image_prompt:
        raise OutfitImagePromptMissingError(f"El outfit {outfit_id} no contiene un prompt visual.")

    # Atajo estable: si el límite ya está persistido, no hace falta reservar.
    image_count = db.scalar(select(func.count(Image.id)).where(Image.outfit_id == outfit_id))
    regenerations_done = max(image_count - 1, 0)

    if regenerations_done >= MAX_REGENERATIONS:
        return RegenerationLimitResponse(
            outfit_id=outfit_id,
            message=f"Se ha alcanzado el límite de {MAX_REGENERATIONS} regeneraciones para este outfit.",
            regeneration_count=regenerations_done,
        )

    lease_token = _acquire_regeneration_lease(db, outfit_id)
    lease_closed = False
    try:
        # Recontar dentro de la sección protegida: otra petición pudo terminar
        # entre el atajo anterior y la adquisición de la reserva.
        image_count = db.scalar(select(func.count(Image.id)).where(Image.outfit_id == outfit_id))
        regenerations_done = max(image_count - 1, 0)
        if regenerations_done >= MAX_REGENERATIONS:
            if not _delete_owned_regeneration_lease(db, outfit_id, lease_token):
                raise RegenerationInProgressError(
                    "La reserva de regeneración dejó de pertenecer a esta petición."
                )
            db.commit()
            lease_closed = True
            return RegenerationLimitResponse(
                outfit_id=outfit_id,
                message=(
                    f"Se ha alcanzado el límite de {MAX_REGENERATIONS} "
                    "regeneraciones para este outfit."
                ),
                regeneration_count=regenerations_done,
            )

        # Reutiliza el prompt guardado y genera sin mantener una transacción ni
        # un lock de escritura abiertos. La fila de lease es solo una señal.
        # Un outfit analizado con generate_image=false aún no tiene imagen: en
        # ese caso esta llamada crea la original y debe usar el prompt exacto.
        # La directiva de variación solo tiene sentido cuando ya existe una.
        generation_prompt = outfit.image_prompt
        if image_count > 0:
            generation_prompt += VARIATION_DIRECTIVE

        image_details = generate_outfit_image(generation_prompt)
        persisted_image = _persist_image(db, outfit_id, image_details, generation_prompt)
        if not _delete_owned_regeneration_lease(db, outfit_id, lease_token):
            db.rollback()
            raise RegenerationInProgressError(
                "La reserva de regeneración caducó antes de guardar la imagen."
            )
        db.commit()
        lease_closed = True

        # Si no había imagen inicial, esta es la original recuperada: 0
        # regeneraciones consumidas y las 3 disponibles. A partir de la segunda
        # imagen sí aumenta el contador.
        new_image_count = image_count + 1
        regenerations_done = max(new_image_count - 1, 0)
        try:
            worn_preview = _worn_view_preview(_load_persisted_extraction(outfit))
        except WornViewUnavailableError:
            worn_preview = None
        return RegenerationResponse(
            outfit_id=outfit_id,
            image_id=persisted_image.id,
            image=image_details,
            generation_prompt=generation_prompt,
            regeneration_count=regenerations_done,
            regenerations_remaining=MAX_REGENERATIONS - regenerations_done,
            worn_view_preview=worn_preview,
        )
    finally:
        if not lease_closed:
            try:
                _release_regeneration_lease(db, outfit_id, lease_token)
            except Exception:
                # No ocultar el error original. El TTL permite recuperar la
                # reserva si también falla la conexión durante la liberación.
                logger.exception(
                    "No se pudo liberar la reserva de regeneración de outfit %s",
                    outfit_id,
                )


def generate_worn_view(
    outfit_id: int,
    image_id: int,
    db: Session,
    current_user: User | None = None,
) -> WornViewResponse | None:
    """Crea una única vista puesta para una composición persistida.

    La operación es idempotente después del éxito y comparte la reserva atómica
    del outfit con las regeneraciones, por lo que nunca solapa dos llamadas de
    imagen pagadas para el mismo outfit.
    """
    source_image = db.scalar(
        select(Image).where(
            Image.id == image_id,
            Image.outfit_id == outfit_id,
        )
    )
    if source_image is None or not _can_access_outfit(source_image.outfit, current_user):
        return None

    existing = db.scalar(select(WornView).where(WornView.source_image_id == image_id))
    if existing is not None:
        return _worn_view_response(
            outfit_id=outfit_id,
            worn_view=existing,
            created=False,
        )

    extraction = _load_persisted_extraction(source_image.outfit)
    generation_prompt = build_worn_view_prompt(extraction)
    try:
        reference_path = resolve_generated_image_path(source_image.path)
    except ValueError as exc:
        raise WornViewUnavailableError(str(exc)) from exc
    if not reference_path.is_file():
        raise WornViewUnavailableError(
            "No se encuentra la composición local necesaria para generar la vista puesta."
        )

    lease_token = _acquire_regeneration_lease(db, outfit_id)
    lease_closed = False
    try:
        # Otra petición pudo terminar entre el atajo y la adquisición.
        existing = db.scalar(select(WornView).where(WornView.source_image_id == image_id))
        if existing is not None:
            if not _delete_owned_regeneration_lease(db, outfit_id, lease_token):
                raise RegenerationInProgressError(
                    "La reserva de generación dejó de pertenecer a esta petición."
                )
            db.commit()
            lease_closed = True
            return _worn_view_response(
                outfit_id=outfit_id,
                worn_view=existing,
                created=False,
            )

        generated = generate_worn_view_image(generation_prompt, reference_path)
        worn_view = WornView(
            source_image_id=image_id,
            path=generated.image.url_or_base64,
            generation_prompt=generation_prompt,
            image_model=generated.image.model,
            quality=generated.image.quality,
            size=generated.image.size,
            cost_estimate=generated.cost_estimate,
        )
        db.add(worn_view)
        db.flush()
        if not _delete_owned_regeneration_lease(db, outfit_id, lease_token):
            db.rollback()
            raise RegenerationInProgressError(
                "La reserva de generación caducó antes de guardar la vista puesta."
            )
        db.commit()
        lease_closed = True
        return _worn_view_response(
            outfit_id=outfit_id,
            worn_view=worn_view,
            created=True,
        )
    finally:
        if not lease_closed:
            try:
                _release_regeneration_lease(db, outfit_id, lease_token)
            except Exception:
                logger.exception(
                    "No se pudo liberar la reserva tras la vista puesta del outfit %s",
                    outfit_id,
                )
