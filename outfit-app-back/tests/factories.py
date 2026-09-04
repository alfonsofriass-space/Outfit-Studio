"""Andamios compartidos por las suites.

Aquí solo entra lo genérico: construir un item, una extracción mínima válida, o las
filas que casi cualquier test necesita antes de poder probar algo.

Lo que NO entra es un fixture que describe el caso concreto de un test —el kimono con
palazzo de las vistas puestas, la extracción con accesorio de la búsqueda de prendas—.
Ese dato vive junto a su test porque leerlo es parte de entender qué se comprueba;
moverlo aquí obligaría a saltar de fichero para entender una sola aserción.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Image, Outfit, User
from app.schemas import OutfitExtraction, OutfitItem


def make_item(
    category: str,
    item_type: str,
    phrase: str | None = None,
    *,
    certainty: str = "high",
    **attributes,
) -> OutfitItem:
    """Un item válido. Sin frase visual explícita se usa el propio tipo de prenda."""
    return OutfitItem(
        category=category,
        item_type=item_type,
        certainty=certainty,
        visual_phrase_en=item_type if phrase is None else phrase,
        **attributes,
    )


def make_extraction(
    *items: OutfitItem,
    summary: str = "look de prueba",
    status: str = "ok",
    styling_notes: list[str] | None = None,
) -> OutfitExtraction:
    return OutfitExtraction(
        status=status,
        outfit_summary=summary,
        items=list(items),
        styling_notes_en=styling_notes or [],
    )


def shirt_and_trousers() -> OutfitExtraction:
    """El outfit mínimo que supera el contrato: dos piezas, ninguna accesoria."""
    return make_extraction(
        make_item("upper", "camisa", "white shirt", color="blanca"),
        make_item("lower", "pantalón", "black trousers", color="negro"),
        summary="Camisa blanca y pantalón negro",
    )


def make_user(db: Session, username: str, *, role: str = "user") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def make_outfit(
    db: Session,
    owner: User,
    description: str,
    *,
    extraction: OutfitExtraction | None = None,
    **columns,
) -> Outfit:
    outfit = Outfit(
        owner_id=owner.id,
        user_description=description,
        outfit_json=(extraction or shirt_and_trousers()).model_dump_json(),
        image_prompt="reviewed prompt",
        text_model="test-model",
        **columns,
    )
    db.add(outfit)
    db.commit()
    return outfit


def make_image(db: Session, outfit: Outfit, name: str, **columns) -> Image:
    image = Image(
        outfit_id=outfit.id,
        path=f"/images/{name}.png",
        generation_prompt="prompt",
        image_model="test-image",
        quality="low",
        size="1024x1024",
        cost_estimate=0.006,
        created_at=datetime.now(timezone.utc),
        **columns,
    )
    db.add(image)
    db.commit()
    return image
