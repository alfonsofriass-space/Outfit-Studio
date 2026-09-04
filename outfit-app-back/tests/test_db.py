from sqlalchemy import text


def test_sqlite_connections_enforce_foreign_keys(db_session):
    assert db_session.scalar(text("PRAGMA foreign_keys")) == 1
