from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.schemas import (
    ClarificationResponse,
    CredentialsRequest,
    FinalOutfitResponse,
    OutfitDetailResponse,
    OutfitRequest,
    OutfitUpdateRequest,
    ProductSearchRequest,
    ProductSearchResponse,
    ProposalChoiceRequest,
    ProposalRequest,
    ProposalSetResponse,
    RegenerationLimitResponse,
    RegenerationResponse,
    UserResponse,
    WornViewResponse,
)
from app.services.auth_service import (
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
    authenticate_user,
    register_user,
)
from app.services.image_access_service import get_accessible_generated_image
from app.services.openai_image import GENERATED_DIR, ImageGenerationError
from app.services.openai_product_search import (
    ProductSearchIncompleteError,
    ProductSearchProviderError,
    ProductSearchServiceUnavailableError,
)
from app.services.openai_proposals import (
    ProposalModelOutputError,
    ProposalProviderError,
    ProposalServiceUnavailableError,
)
from app.services.openai_text import (
    TextModelOutputError,
    TextProviderError,
    TextServiceUnavailableError,
)
from app.services.outfit_operation_lease import OutfitOperationInProgressError
from app.services.outfit_service import (
    OutfitImageNotInOutfitError,
    OutfitImagePromptMissingError,
    RegenerationInProgressError,
    WornViewUnavailableError,
    delete_persisted_outfit,
    generate_worn_view,
    get_persisted_outfit,
    list_persisted_outfits,
    process_outfit_request,
    regenerate_outfit_image,
    update_persisted_outfit,
)
from app.services.product_search_service import (
    ProductSearchDataError,
    ProductSearchInputError,
    ProductSearchLimitError,
    search_persisted_outfit_item,
)
from app.services.proposal_service import (
    ProposalSetUnavailableError,
    choose_proposal,
    get_proposal_set,
    propose_outfits,
)
from app.services.user_operation_lease import UserOperationInProgressError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Valida secretos y configuración antes de aceptar la primera petición.
    # El esquema se gestiona explícitamente con `alembic upgrade head`.
    get_settings().validate_for_startup()
    yield


app = FastAPI(
    title="Outfit MVP Backend",
    description="Backend para estructurar descripciones de outfits y generar imágenes",
    version="1.0.0",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret.get_secret_value(),
    session_cookie="outfit_session",
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=_settings.session_cookie_secure,
)

GENERATED_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health_check():
    return JSONResponse(content={"status": "ok"})


@app.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    credentials: CredentialsRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = register_user(credentials, db)
    except UsernameAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese nombre de usuario ya está en uso.",
        ) from exc
    request.session.clear()
    request.session["user_id"] = user.id
    return user


@app.post("/auth/login", response_model=UserResponse)
def login(
    credentials: CredentialsRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        user = authenticate_user(credentials, db)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
        ) from exc
    request.session.clear()
    request.session["user_id"] = user.id
    return user


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/auth/me", response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)):
    return user


@app.get("/images/{filename}", response_class=FileResponse)
def generated_image(
    filename: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    image_path = get_accessible_generated_image(f"/images/{filename}", user, db)
    if image_path is None:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return FileResponse(image_path, media_type="image/png")


@app.post("/outfits/generate", response_model=FinalOutfitResponse | ClarificationResponse)
def generate_outfit(
    request: OutfitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return process_outfit_request(request, db, user)
    except TextServiceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="El servicio de análisis de outfits no está disponible temporalmente.",
        ) from exc
    except (TextProviderError, TextModelOutputError) as exc:
        raise HTTPException(
            status_code=502,
            detail="No se pudo procesar la respuesta del servicio de análisis.",
        ) from exc


@app.post(
    "/outfits/proposals",
    response_model=ProposalSetResponse | ClarificationResponse,
)
def create_outfit_proposals(
    request: ProposalRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vía de inspiración: una situación entra, tres propuestas salen.

    Se declara antes que las rutas con `{outfit_id}` para que "proposals" nunca se
    interprete como un identificador.
    """
    try:
        return propose_outfits(request, db, user)
    except UserOperationInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya hay una petición de propuestas en curso. "
                "Espera a que termine antes de volver a intentarlo."
            ),
        ) from exc
    except ProposalServiceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="El servicio de propuestas no está disponible temporalmente.",
        ) from exc
    except (ProposalProviderError, ProposalModelOutputError) as exc:
        raise HTTPException(
            status_code=502,
            detail="No se pudo obtener un conjunto de propuestas utilizable.",
        ) from exc


@app.get("/outfits/proposals/{proposal_set_id}", response_model=ProposalSetResponse)
def outfit_proposals(
    proposal_set_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = get_proposal_set(proposal_set_id, db, user)
    except ProposalSetUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail="Este conjunto de propuestas no se puede leer y no puede reutilizarse.",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Conjunto de propuestas no encontrado")
    return result


@app.post(
    "/outfits/proposals/{proposal_set_id}/choose",
    response_model=FinalOutfitResponse,
)
def choose_outfit_proposal(
    proposal_set_id: int,
    request: ProposalChoiceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Promociona una propuesta a outfit. No llama a ningún proveedor."""
    try:
        result = choose_proposal(proposal_set_id, request, db, user)
    except ProposalSetUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail="Esta propuesta no se puede convertir en un outfit generable.",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada")
    return result


@app.get("/outfits", response_model=list[OutfitDetailResponse])
def outfits(
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    favourites_only: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_persisted_outfits(
        db,
        limit=limit,
        current_user=user,
        offset=offset,
        favourites_only=favourites_only,
    )


@app.get("/outfits/{outfit_id}", response_model=OutfitDetailResponse)
def outfit_detail(
    outfit_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = get_persisted_outfit(outfit_id, db, user)
    if result is None:
        raise HTTPException(status_code=404, detail="Outfit no encontrado")
    return result


@app.post(
    "/outfits/{outfit_id}/items/{item_index}/product-search",
    response_model=ProductSearchResponse,
)
def create_product_search(
    outfit_id: int,
    item_index: int,
    request: ProductSearchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = search_persisted_outfit_item(outfit_id, item_index, request, db, user)
    except ProductSearchLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProductSearchInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProductSearchDataError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutfitOperationInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya hay otra operación de pago en curso para este outfit. "
                "Espera a que termine antes de buscar otra prenda."
            ),
        ) from exc
    except ProductSearchServiceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "El servicio de búsqueda de productos no está disponible temporalmente. "
                "No se ha realizado un reintento automático."
            ),
        ) from exc
    except ProductSearchIncompleteError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "La búsqueda terminó antes de devolver resultados completos. "
                "No se ha realizado un reintento automático."
            ),
        ) from exc
    except ProductSearchProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "La búsqueda no devolvió un resultado utilizable. "
                "No se ha realizado un reintento automático."
            ),
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Outfit o prenda no encontrados")
    return result


@app.patch("/outfits/{outfit_id}", response_model=OutfitDetailResponse)
def update_outfit(
    outfit_id: int,
    request: OutfitUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marca la portada elegida o el favorito. No llama a ningún proveedor."""
    try:
        result = update_persisted_outfit(outfit_id, request, db, user)
    except OutfitImageNotInOutfitError as exc:
        raise HTTPException(
            status_code=409,
            detail="Esa composición no pertenece a este outfit.",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Outfit no encontrado")
    return result


@app.delete("/outfits/{outfit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_outfit(
    outfit_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        deleted = delete_persisted_outfit(outfit_id, db, user)
    except RegenerationInProgressError:
        raise HTTPException(
            status_code=409,
            detail=(
                "No se puede eliminar este outfit mientras tiene una generación de imagen en curso."
            ),
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Outfit no encontrado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/outfits/{outfit_id}/regenerate",
    response_model=RegenerationResponse | RegenerationLimitResponse,
)
def regenerate_outfit(
    outfit_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = regenerate_outfit_image(outfit_id, db, user)
    except RegenerationInProgressError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya hay una generación en curso para este outfit. "
                "Espera a que termine antes de volver a intentarlo."
            ),
        )
    except OutfitImagePromptMissingError as exc:
        raise HTTPException(
            status_code=409,
            detail="Este outfit no tiene un prompt visual válido y no puede generar una imagen.",
        ) from exc
    except ImageGenerationError:
        # El intento fallido no persiste nada ni consume el límite de regeneraciones.
        raise HTTPException(
            status_code=502,
            detail="No se pudo generar la imagen. Inténtalo de nuevo; este intento no consume regeneraciones.",
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Outfit no encontrado")
    return result


@app.post(
    "/outfits/{outfit_id}/images/{image_id}/worn-view",
    response_model=WornViewResponse,
)
def create_worn_view(
    outfit_id: int,
    image_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = generate_worn_view(outfit_id, image_id, db, user)
    except RegenerationInProgressError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya hay una generación en curso para este outfit. "
                "Espera a que termine antes de volver a intentarlo."
            ),
        )
    except WornViewUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImageGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo generar la vista puesta. No se guardó ningún resultado "
                "y no habrá un reintento automático."
            ),
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Outfit o imagen no encontrados")
    return result
