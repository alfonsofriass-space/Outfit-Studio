import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, _set_sqlite_pragmas


@pytest.fixture
def db_session():
    """
    BD SQLite en memoria aislada por test. StaticPool asegura que todas las
    conexiones comparten la MISMA base en memoria (si no, cada conexión ve una vacía).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    import app.models  # noqa: F401  registra los modelos en Base.metadata

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
