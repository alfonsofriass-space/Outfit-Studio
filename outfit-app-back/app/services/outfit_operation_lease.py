import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import RegenerationLease

# El timeout más largo actual es el de imagen (120 s). Diez minutos evita
# recuperar una reserva mientras su propietario legítimo aún espera al
# proveedor y desbloquea el outfit tras la caída abrupta de un worker.
OUTFIT_OPERATION_LEASE_TTL = timedelta(minutes=10)


class OutfitOperationInProgressError(Exception):
    """Ya hay otra operación pagada o un borrado activo para el outfit."""


def _try_create_lease(
    db: Session,
    outfit_id: int,
    token: str,
    acquired_at: datetime,
) -> bool:
    db.add(
        RegenerationLease(
            outfit_id=outfit_id,
            token=token,
            acquired_at=acquired_at,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # La PK outfit_id arbitra de forma atómica entre sesiones y procesos.
        db.rollback()
        return False
    return True


def acquire_outfit_operation_lease(db: Session, outfit_id: int) -> str:
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    if _try_create_lease(db, outfit_id, token, now):
        return token

    # Una caída del proceso no ejecuta finally. Solo se recupera una reserva
    # inequívocamente caducada y se vuelve a competir por la PK una vez.
    stale_before = now - OUTFIT_OPERATION_LEASE_TTL
    deleted = db.execute(
        delete(RegenerationLease).where(
            RegenerationLease.outfit_id == outfit_id,
            RegenerationLease.acquired_at < stale_before,
        )
    ).rowcount
    db.commit()
    if deleted and _try_create_lease(
        db,
        outfit_id,
        token,
        datetime.now(timezone.utc),
    ):
        return token

    raise OutfitOperationInProgressError(
        f"Ya hay otra operación en curso para el outfit {outfit_id}."
    )


def delete_owned_outfit_operation_lease(
    db: Session,
    outfit_id: int,
    token: str,
) -> bool:
    result = db.execute(
        delete(RegenerationLease).where(
            RegenerationLease.outfit_id == outfit_id,
            RegenerationLease.token == token,
        )
    )
    return result.rowcount == 1


def release_outfit_operation_lease(
    db: Session,
    outfit_id: int,
    token: str,
) -> None:
    # Descarta cualquier transacción incompleta antes de abrir la liberación.
    db.rollback()
    delete_owned_outfit_operation_lease(db, outfit_id, token)
    db.commit()
