from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import User
from app.services.auth_service import password_hash


def _client_for(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_register_creates_a_normal_user_and_starts_a_session(db_session):
    with _client_for(db_session) as client:
        response = client.post(
            "/auth/register",
            json={"username": "Alfonso", "password": "test"},
        )

        assert response.status_code == 201
        assert response.json()["username"] == "alfonso"
        assert response.json()["role"] == "user"
        assert "password" not in response.json()
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

        persisted = db_session.query(User).filter_by(username="alfonso").one()
        assert persisted.password_hash != "test"
        assert persisted.password_hash.startswith("$argon2id$")

        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == persisted.id

    app.dependency_overrides.clear()


def test_register_rejects_a_duplicate_normalized_username(db_session):
    with _client_for(db_session) as client:
        first = client.post(
            "/auth/register",
            json={"username": "persona", "password": "clave"},
        )
        duplicate = client.post(
            "/auth/register",
            json={"username": "PERSONA", "password": "otra"},
        )

        assert first.status_code == 201
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "Ese nombre de usuario ya está en uso."}

    app.dependency_overrides.clear()


def test_register_cannot_choose_the_admin_role(db_session):
    with _client_for(db_session) as client:
        response = client.post(
            "/auth/register",
            json={"username": "persona", "password": "clave", "role": "admin"},
        )

        assert response.status_code == 422
        assert db_session.query(User).count() == 0

    app.dependency_overrides.clear()


def test_login_and_logout_use_the_existing_account(db_session):
    user = User(
        username="persona",
        password_hash=password_hash.hash("clave"),
        role="user",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    with _client_for(db_session) as client:
        login = client.post(
            "/auth/login",
            json={"username": "PERSONA", "password": "clave"},
        )
        assert login.status_code == 200
        assert login.json()["id"] == user.id

        logout = client.post("/auth/logout")
        assert logout.status_code == 204
        assert client.get("/auth/me").status_code == 401

    app.dependency_overrides.clear()


def test_login_does_not_reveal_which_credential_is_wrong(db_session):
    user = User(
        username="persona",
        password_hash=password_hash.hash("clave"),
        role="user",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    with _client_for(db_session) as client:
        wrong_password = client.post(
            "/auth/login",
            json={"username": "persona", "password": "fallo"},
        )
        unknown_user = client.post(
            "/auth/login",
            json={"username": "desconocido", "password": "fallo"},
        )

        assert wrong_password.status_code == 401
        assert unknown_user.status_code == 401
        assert wrong_password.json() == unknown_user.json()

    app.dependency_overrides.clear()


def test_me_requires_a_session(db_session):
    with _client_for(db_session) as client:
        response = client.get("/auth/me")

        assert response.status_code == 401
        assert response.json() == {"detail": "Debes iniciar sesión."}

    app.dependency_overrides.clear()


def test_outfit_endpoints_require_a_session(db_session):
    with _client_for(db_session) as client:
        response = client.get("/outfits")

        assert response.status_code == 401
        assert response.json() == {"detail": "Debes iniciar sesión."}

    app.dependency_overrides.clear()
