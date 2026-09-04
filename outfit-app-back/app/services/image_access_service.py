from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Image, Outfit, User, WornView
from app.services.openai_image import resolve_generated_image_path


def get_accessible_generated_image(
    public_path: str,
    current_user: User,
    db: Session,
) -> Path | None:
    """Resuelve una imagen local solo cuando pertenece a un outfit accesible."""
    try:
        file_path = resolve_generated_image_path(public_path)
    except ValueError:
        return None

    owner_row = db.execute(
        select(Outfit.owner_id)
        .join(Image, Image.outfit_id == Outfit.id)
        .where(Image.path == public_path)
        .limit(1)
    ).first()
    if owner_row is None:
        owner_row = db.execute(
            select(Outfit.owner_id)
            .join(Image, Image.outfit_id == Outfit.id)
            .join(WornView, WornView.source_image_id == Image.id)
            .where(WornView.path == public_path)
            .limit(1)
        ).first()

    if owner_row is None:
        return None
    owner_id = owner_row[0]
    if current_user.role != "admin" and owner_id != current_user.id:
        return None
    return file_path if file_path.is_file() else None
