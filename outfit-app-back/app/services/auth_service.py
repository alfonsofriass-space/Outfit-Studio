from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import CredentialsRequest

password_hash = PasswordHash.recommended()


class UsernameAlreadyExistsError(Exception):
    """El nombre normalizado ya pertenece a otra cuenta."""


class InvalidCredentialsError(Exception):
    """El usuario o la contraseña no permiten iniciar sesión."""


def register_user(credentials: CredentialsRequest, db: Session) -> User:
    existing = db.scalar(select(User).where(User.username == credentials.username))
    if existing is not None:
        raise UsernameAlreadyExistsError

    user = User(
        username=credentials.username,
        password_hash=password_hash.hash(credentials.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameAlreadyExistsError from exc
    db.refresh(user)
    return user


def authenticate_user(credentials: CredentialsRequest, db: Session) -> User:
    user = db.scalar(select(User).where(User.username == credentials.username))
    if user is None or not user.is_active:
        raise InvalidCredentialsError

    try:
        is_valid = password_hash.verify(credentials.password, user.password_hash)
    except (TypeError, ValueError):
        is_valid = False
    if not is_valid:
        raise InvalidCredentialsError
    return user
