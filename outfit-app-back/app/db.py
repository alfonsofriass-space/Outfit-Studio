from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

# SQLite local por defecto para el MVP. Migrar a Postgres/Supabase en producción
# es solo cambiar DATABASE_URL (el resto del código usa SQLAlchemy y no cambia).
# NOTA: no usar "sqlite:///:memory:" aquí — con el pool por defecto cada conexión
# crea su propia BD vacía y las tablas "desaparecen" entre requests. Para tests en
# memoria usar StaticPool (ver tests/conftest.py).
DATABASE_URL = get_settings().database_url

# check_same_thread solo aplica a SQLite; permite usar la conexión desde FastAPI.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)


def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """Activa integridad referencial y tolerancia a concurrencia en SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


if DATABASE_URL.startswith("sqlite"):
    event.listen(engine, "connect", _set_sqlite_pragmas)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependencia de FastAPI: abre una sesión por request y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
