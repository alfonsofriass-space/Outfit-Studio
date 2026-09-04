from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Cuenta local con uno de los dos roles cerrados del MVP."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    outfits: Mapped[list["Outfit"]] = relationship(back_populates="owner")


class Outfit(Base):
    """
    Una petición de outfit: la descripción del usuario, el análisis estructurado
    (JSON) y el image_prompt que se reutiliza al regenerar. Tiene N imágenes.
    """

    __tablename__ = "outfits"
    # Elegir dos veces la misma propuesta devuelve el outfit que ya existe en vez de
    # duplicarlo. Los outfits escritos a mano dejan ambas columnas a NULL y SQL
    # permite repetir NULL, así que la restricción solo afecta a la vía de inspiración.
    __table_args__ = (
        UniqueConstraint(
            "proposal_set_id",
            "proposal_index",
            name="uq_outfits_proposal_set_id_proposal_index",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable solo para poder leer bases históricas durante la migración. Toda
    # creación desde la API asigna siempre el usuario autenticado.
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_outfits_owner_id_users",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    user_description: Mapped[str] = mapped_column(Text, nullable=False)
    outfit_json: Mapped[str] = mapped_column(Text, nullable=False)  # OutfitExtraction serializado
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_model: Mapped[str] = mapped_column(String(64), nullable=False)
    # Origen del outfit cuando viene de la vía de inspiración. NULL en la vía de
    # descripción. El conjunto sobrevive al borrado del outfit: la llamada ya se pagó
    # y su coste medido no debe desaparecer con la imagen.
    proposal_set_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "proposal_sets.id",
            name="fk_outfits_proposal_set_id_proposal_sets",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    proposal_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_favourite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    owner: Mapped["User | None"] = relationship(back_populates="outfits")
    images: Mapped[list["Image"]] = relationship(
        back_populates="outfit",
        cascade="all, delete-orphan",
        order_by="Image.created_at",
    )
    product_searches: Mapped[list["ProductSearch"]] = relationship(
        back_populates="outfit",
        cascade="all, delete-orphan",
        order_by="ProductSearch.item_index",
    )


class Image(Base):
    """
    Una imagen generada para un outfit. La primera fila es la original; cada
    regeneración añade una fila más. Nº de regeneraciones = COUNT(images) - 1.
    """

    __tablename__ = "images"
    # Como máximo una composición elegida por outfit. Un índice único parcial deja que
    # sea la base quien lo garantice, igual que la reserva por outfit o la unicidad de
    # los intentos de búsqueda.
    __table_args__ = (
        Index(
            "uq_images_chosen_per_outfit",
            "outfit_id",
            unique=True,
            sqlite_where=text("is_chosen = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outfit_id: Mapped[int] = mapped_column(
        ForeignKey(
            "outfits.id",
            name="fk_images_outfit_id_outfits",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # ruta propia, NO la URL de OpenAI
    generation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_model: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[str] = mapped_column(String(16), nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # La composición que el usuario marcó como la buena de su outfit. Vive aquí y no en
    # `outfits` para no crear un ciclo de claves foráneas entre las dos tablas:
    # SQLAlchemy no puede ordenarlas y avisa de que eso será un error en el futuro.
    is_chosen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    outfit: Mapped["Outfit"] = relationship(back_populates="images")
    worn_view: Mapped["WornView | None"] = relationship(
        back_populates="source_image",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )


class WornView(Base):
    """Vista puesta derivada de una composición concreta.

    Vive fuera de ``images`` para no alterar la semántica ni el límite de
    regeneraciones. La unicidad hace idempotente la operación por imagen fuente.
    """

    __tablename__ = "worn_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    generation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    image_model: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[str] = mapped_column(String(16), nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    source_image: Mapped["Image"] = relationship(back_populates="worn_view")


class ProductSearch(Base):
    """Resultado persistido de un intento de búsqueda web para una prenda.

    ``item_index`` apunta a la posición estable dentro del ``outfit_json``.
    Solo se insertan búsquedas completadas, también cuando no hay candidatos.
    ``attempt`` empieza en 1 y crece con cada búsqueda repetida de la misma prenda:
    el historial se conserva porque cada intento es una llamada pagada, igual que
    ocurre con las regeneraciones de imagen.
    """

    __tablename__ = "product_searches"
    __table_args__ = (
        UniqueConstraint(
            "outfit_id",
            "item_index",
            "attempt",
            name="uq_product_searches_outfit_id_item_index_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outfit_id: Mapped[int] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    additional_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False)
    search_model: Mapped[str] = mapped_column(String(64), nullable=False)
    web_search_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    outfit: Mapped["Outfit"] = relationship(back_populates="product_searches")


class RegenerationLease(Base):
    """Reserva efímera que serializa operaciones pagadas y borrado por outfit.

    ``outfit_id`` es la clave primaria: la base de datos arbitra la carrera entre
    procesos. El token evita que una petición libere una reserva que ya haya sido
    recuperada por otra tras caducar. El nombre de tabla se conserva por
    compatibilidad histórica aunque la reserva ya no sea exclusiva de imágenes.
    """

    __tablename__ = "regeneration_leases"

    outfit_id: Mapped[int] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        nullable=False,
    )


class ProposalSet(Base):
    """Tres propuestas generadas a partir de una situación, en UNA sola fila.

    Proponer no crea outfits: si cada candidata escribiese su propia fila, cada
    petición dejaría dos análisis huérfanos en la biblioteca. Solo la propuesta
    elegida promociona, y lo hace apuntando desde ``outfits`` hacia aquí: así una
    segunda propuesta del mismo conjunto también puede generarse más tarde sin
    volver a pagar, en vez de quedar bloqueada por la primera elección.
    ``cost_estimate`` se calcula desde el usage devuelto, nunca se estima antes.
    """

    __tablename__ = "proposal_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_proposal_sets_owner_id_users",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    situation: Mapped[str] = mapped_column(Text, nullable=False)
    proposals_json: Mapped[str] = mapped_column(Text, nullable=False)
    text_model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class UserOperationLease(Base):
    """Reserva efímera que serializa las operaciones pagadas sin outfit todavía.

    ``RegenerationLease`` arbitra por ``outfit_id``, así que no puede cubrir una
    petición de propuestas: cuando se paga esa llamada aún no existe ningún outfit.
    Esta tabla conserva el mismo invariante con ``user_id`` como clave primaria.
    """

    __tablename__ = "user_operation_leases"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        nullable=False,
    )
