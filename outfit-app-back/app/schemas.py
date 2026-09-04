from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Número de opciones que devuelve la vía de inspiración. Tres son suficientes para
# elegir sin convertir la elección en otro problema, y el coste es de una sola llamada.
PROPOSALS_PER_SET = 3


# ==========================================
# 1. Modelos de Entrada (Request)
# ==========================================
class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=4, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class OutfitRequest(BaseModel):
    # max_length: sin límite, un input de 500 KB que contenga 2 keywords pasa la
    # heurística y viaja entero a la API como tokens (coste arbitrario controlado
    # por el cliente). Ninguna descripción legítima de un outfit supera 500 chars.
    # Producto solo-español por ahora (decisión 2026-07-08): la heurística pre-LLM
    # y los prompts asumen español. El antiguo campo 'language' se aceptaba pero se
    # ignoraba (una descripción en inglés recibía siempre needs_clarification), así
    # que se eliminó del contrato en vez de prometer algo que no cumplía.
    user_description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Descripción del outfit introducida por el usuario (en español)",
    )
    generate_image: bool = Field(
        default=True, description="Si se debe generar imagen o solo estructurar el texto"
    )
    # Reanaliza sobre un análisis propio que todavía no tiene composiciones ni
    # búsquedas, en vez de dejarlo huérfano y crear otra fila. Editar la descripción
    # deja de acumular análisis casi idénticos en la biblioteca.
    replace_outfit_id: Optional[int] = Field(
        default=None,
        gt=0,
        description="Análisis propio y sin resultados que debe reutilizarse en vez de crear otro",
    )


class ProductSearchRequest(BaseModel):
    additional_details: Optional[str] = Field(
        default=None,
        max_length=200,
        description=(
            "Marca o cualquier otro detalle opcional escrito por el usuario "
            "para completar o afinar la búsqueda de una prenda"
        ),
    )
    # Sin esta bandera, repetir la petición devuelve la búsqueda guardada sin pagar.
    # Una recarga, un doble clic o un reintento accidental nunca gastan; solo puede
    # crear un intento nuevo una acción explícita del usuario.
    force_new: bool = Field(
        default=False,
        description="Ejecuta una búsqueda nueva en vez de devolver la última guardada",
    )

    @field_validator("additional_details", mode="before")
    @classmethod
    def normalize_product_search_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None


class ProposalRequest(BaseModel):
    """Entrada de la vía de inspiración: una situación, no un outfit."""

    model_config = ConfigDict(extra="forbid")

    # Mismo techo que `user_description` y por el mismo motivo: el texto viaja
    # entero al modelo como tokens y su tamaño no puede quedar en manos del cliente.
    situation: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Situación para la que se piden propuestas (en español)",
    )


class OutfitUpdateRequest(BaseModel):
    """Cambia metadatos de un outfit. No hay pago detrás, así que no es una acción."""

    model_config = ConfigDict(extra="forbid")

    # `None` explícito quita la portada elegida; omitir el campo lo deja como está.
    chosen_image_id: Optional[int] = Field(default=None, gt=0)
    is_favourite: Optional[bool] = None

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> "OutfitUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Indica al menos un campo que cambiar")
        return self


class ProposalChoiceRequest(BaseModel):
    """Elección de una de las tres propuestas ya persistidas."""

    model_config = ConfigDict(extra="forbid")

    proposal_index: int = Field(..., ge=0, description="Posición de la propuesta elegida")


# ==========================================
# 2. Modelos Internos (Salida del LLM)
# ==========================================
class OutfitItem(BaseModel):
    category: Literal["upper", "lower", "one_piece", "footwear", "accessory"]
    item_type: str
    brand: Optional[str] = Field(default=None, max_length=80)
    color: Optional[str] = None
    material: Optional[str] = None
    fit: Optional[str] = None
    details: List[str] = Field(default_factory=list)
    certainty: Literal["high", "medium", "low"]
    visual_phrase_en: str = Field(
        ...,
        description="Frase visual corta EN INGLÉS para la generación de imagen, "
        "solo con atributos presentes en el texto. "
        "Ej: 'black bomber jacket with white floral embroidery on the sleeves'",
    )

    @field_validator("visual_phrase_en")
    @classmethod
    def validate_visual_phrase(cls, value: str) -> str:
        phrase = value.strip()
        if not phrase:
            raise ValueError("visual_phrase_en no puede estar vacía")
        return phrase


class OutfitExtraction(BaseModel):
    """
    Este es el JSON que devolverá el modelo de texto.
    El prompt de imagen NO lo escribe el modelo: se compone en código a partir de
    los items y las relaciones de estilo explícitas (ver
    app/prompts/image_prompt_builder.py). Las restricciones visuales son constantes
    de producto y viven también en el builder, no en este schema.
    """

    status: Literal["ok", "needs_clarification"]
    outfit_summary: str
    items: List[OutfitItem] = Field(default_factory=list)
    styling_notes_en: List[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Relaciones visuales cortas en inglés expresadas explícitamente por el usuario, "
            "como superposición, uso alternativo o un par desparejado."
        ),
    )

    @field_validator("styling_notes_en")
    @classmethod
    def validate_styling_notes(cls, value: List[str]) -> List[str]:
        notes = [note.strip() for note in value]
        if any(not note for note in notes):
            raise ValueError("styling_notes_en no puede contener instrucciones vacías")
        return notes


class ProposedOutfit(BaseModel):
    """Una de las tres propuestas.

    Comparte estructura con `OutfitExtraction` menos el estado, que aquí no aplica:
    una propuesta ofrecida siempre es un outfit completo. `to_extraction` la
    convierte en el objeto del que ya cuelga todo lo pagado, sin transformar items.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Nombre corto en español que distingue la propuesta",
    )
    outfit_summary: str
    items: List[OutfitItem] = Field(default_factory=list)
    styling_notes_en: List[str] = Field(default_factory=list, max_length=8)

    @field_validator("styling_notes_en")
    @classmethod
    def validate_proposal_styling_notes(cls, value: List[str]) -> List[str]:
        notes = [note.strip() for note in value]
        if any(not note for note in notes):
            raise ValueError("styling_notes_en no puede contener instrucciones vacías")
        return notes

    def to_extraction(self) -> "OutfitExtraction":
        return OutfitExtraction(
            status="ok",
            outfit_summary=self.outfit_summary,
            items=self.items,
            styling_notes_en=self.styling_notes_en,
        )


class ProposalSetExtraction(BaseModel):
    """JSON que devuelve el modelo de propuestas.

    El recuento exacto es parte del contrato: una salida con otro número de
    propuestas es inusable y se trata como tal, igual que un JSON no parseable.
    """

    status: Literal["ok", "needs_clarification"]
    proposals: List[ProposedOutfit] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_proposal_count(self) -> "ProposalSetExtraction":
        if self.status == "ok" and len(self.proposals) != PROPOSALS_PER_SET:
            raise ValueError(f"Una propuesta válida debe traer {PROPOSALS_PER_SET} opciones")
        return self


# ==========================================
# 3. Modelos de Salida (Response)
# ==========================================
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Literal["admin", "user"]
    is_active: bool
    created_at: datetime


class ImageDetails(BaseModel):
    model: str
    quality: str
    size: str
    url_or_base64: str


class ModelsUsed(BaseModel):
    text_primary: str
    text_fallback: Optional[str] = None
    image: Optional[str] = None


class WornViewPreview(BaseModel):
    generation_prompt: str
    estimated_cost: float


class WornViewDetails(BaseModel):
    worn_view_id: int
    source_image_id: int
    generation_prompt: str
    image: ImageDetails
    cost_estimate: float
    created_at: datetime


class OutfitImageResponse(BaseModel):
    """Composición persistida dentro del detalle de un outfit."""

    image_id: int
    generation_number: int
    generation_prompt: Optional[str] = None
    image: ImageDetails
    cost_estimate: float
    created_at: datetime
    worn_view: Optional[WornViewDetails] = None


class ProductCandidate(BaseModel):
    title: str
    store: str
    product_url: str
    price_text: Optional[str] = None


class ProductSearchDetails(BaseModel):
    item_index: int
    attempt: int = 1
    query: str
    additional_details: Optional[str] = None
    candidates: List[ProductCandidate] = Field(default_factory=list)
    model: str
    web_search_calls: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float
    created_at: datetime


class ProductSearchItemState(BaseModel):
    item_index: int
    query: Optional[str] = None
    needs_details: bool
    message: Optional[str] = None
    estimated_cost: float
    # `search` es el intento más reciente; `attempts_remaining` indica cuántas
    # búsquedas de pago quedan disponibles para esta prenda.
    search: Optional[ProductSearchDetails] = None
    attempts: int = 0
    attempts_remaining: int = 0


class OutfitDetailResponse(BaseModel):
    """Representación única para listar, recuperar y continuar un outfit."""

    outfit_id: int
    user_description: str
    outfit: OutfitExtraction
    image_prompt: Optional[str] = None
    text_model: str
    flat_lay_estimated_cost: float
    accessories_omitted: List[str] = Field(default_factory=list)
    worn_view_preview: Optional[WornViewPreview] = None
    product_search_items: List[ProductSearchItemState] = Field(default_factory=list)
    images: List[OutfitImageResponse] = Field(default_factory=list)
    # Composición que el usuario marcó como la buena. `None` significa que todavía no ha
    # elegido: qué miniatura enseñar entonces lo decide el cliente, no el backend.
    chosen_image_id: Optional[int] = None
    is_favourite: bool = False
    regeneration_count: int
    regenerations_remaining: int
    created_at: datetime


class FinalOutfitResponse(BaseModel):
    status: Literal["completed"] = "completed"
    outfit_id: int
    # Texto original controlado por la aplicación; nunca se pide al modelo que
    # lo repita o lo reformule.
    user_description: str
    outfit: OutfitExtraction
    image: Optional[ImageDetails] = None
    image_id: Optional[int] = None
    # Si la generación de imagen falló: el outfit se guarda igualmente y el
    # cliente puede regenerar. image=None + este mensaje (nunca un path falso).
    image_error: Optional[str] = None
    # Prompt exacto que recibirá (o recibió) el modelo de imagen. Permite que el
    # frontend lo enseñe antes de confirmar una generación de pago.
    image_prompt: str
    # Estimación calculada por el backend a partir de la configuración visual
    # activa. El frontend no replica tarifas ni presupone calidad o tamaño.
    flat_lay_estimated_cost: float
    # Accesorios fuera del board por el cap de composición (se informan, no se
    # recortan en silencio).
    accessories_omitted: List[str] = Field(default_factory=list)
    worn_view_preview: Optional[WornViewPreview] = None
    # Mismo estado de búsqueda que el detalle persistido, para que el flujo de
    # creación pueda ofrecer la búsqueda de prendas sin volver a consultar la lista.
    product_search_items: List[ProductSearchItemState] = Field(default_factory=list)
    models_used: ModelsUsed


class ClarificationResponse(BaseModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    message: str
    suggestion: str
    # Cuando el rechazo local se debe a que el texto describe una situación y no un
    # outfit, la respuesta ofrece la otra vía en vez de un error seco. Opcional para
    # no alterar el contrato existente ni obligar al cliente a conocerla.
    suggested_mode: Optional[Literal["inspiration"]] = None


class ProposalDetails(BaseModel):
    """Una propuesta tal y como la recibe el cliente, con su posición estable."""

    index: int
    title: str
    outfit_summary: str
    items: List[OutfitItem] = Field(default_factory=list)
    styling_notes_en: List[str] = Field(default_factory=list)


class ProposalSetResponse(BaseModel):
    status: Literal["proposals_ready"] = "proposals_ready"
    proposal_set_id: int
    # Texto original del usuario, conservado por la aplicación: nunca se pide al
    # modelo que lo repita ni se reconstruye desde la salida.
    situation: str
    proposals: List[ProposalDetails]
    # Coste medido desde el usage de la llamada, no una estimación previa.
    cost_estimate: float
    # Posiciones que ya tienen outfit. Elegir una no bloquea las otras dos: volver
    # a por la segunda propuesta no cuesta otra llamada.
    chosen_indexes: List[int] = Field(default_factory=list)
    created_at: datetime
    models_used: ModelsUsed


class RegenerationResponse(BaseModel):
    status: Literal["regenerated"] = "regenerated"
    outfit_id: int
    image_id: int
    image: ImageDetails
    generation_prompt: str
    regeneration_count: int  # cuántas regeneraciones se han hecho ya (sin contar la original)
    regenerations_remaining: int
    worn_view_preview: Optional[WornViewPreview] = None


class RegenerationLimitResponse(BaseModel):
    status: Literal["regeneration_limit_reached"] = "regeneration_limit_reached"
    outfit_id: int
    message: str
    regeneration_count: int


class WornViewResponse(BaseModel):
    status: Literal["worn_view_ready"] = "worn_view_ready"
    created: bool
    outfit_id: int
    source_image_id: int
    worn_view: WornViewDetails


class ProductSearchResponse(BaseModel):
    status: Literal["product_search_ready"] = "product_search_ready"
    created: bool
    outfit_id: int
    item_index: int
    search: ProductSearchDetails
