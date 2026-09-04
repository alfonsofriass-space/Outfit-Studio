import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import UserOperationLease

# Mismo TTL que la reserva por outfit: el timeout de texto es mucho más corto, pero
# diez minutos evita recuperar una reserva mientras su dueño legítimo sigue esperando
# al proveedor y desbloquea al usuario tras la caída abrupta de un worker.
USER_OPERATION_LEASE_TTL = timedelta(minutes=10)


class UserOperationInProgressError(Exception):
    """Ya hay otra operación pagada sin outfit en curso para este usuario."""


def _try_create_lease(
    db: Session,
    user_id: int,
    token: str,
    acquired_at: datetime,
) -> bool:
    db.add(
        UserOperationLease(
            user_id=user_id,
            token=token,
            acquired_at=acquired_at,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # La PK user_id arbitra de forma atómica entre sesiones y procesos.
        db.rollback()
        return False
    return True


def acquire_user_operation_lease(db: Session, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    if _try_create_lease(db, user_id, token, now):
        return token

    # Una caída del proceso no ejecuta finally. Solo se recupera una reserva
    # inequívocamente caducada y se vuelve a competir por la PK una vez.
    stale_before = now - USER_OPERATION_LEASE_TTL
    deleted = db.execute(
        delete(UserOperationLease).where(
            UserOperationLease.user_id == user_id,
            UserOperationLease.acquired_at < stale_before,
        )
    ).rowcount
    db.commit()
    if deleted and _try_create_lease(
        db,
        user_id,
        token,
        datetime.now(timezone.utc),
    ):
        return token

    raise UserOperationInProgressError(f"Ya hay otra operación en curso para el usuario {user_id}.")


def release_user_operation_lease(db: Session, user_id: int, token: str) -> None:
    # Descarta cualquier transacción incompleta antes de abrir la liberación.
    db.rollback()
    db.execute(
        delete(UserOperationLease).where(
            UserOperationLease.user_id == user_id,
            UserOperationLease.token == token,
        )
    )
    db.commit()
