from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, Settings

PROJECT_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_TEXT_MODEL_PRIMARY",
    "OPENAI_TEXT_MODEL_FALLBACK",
    "OPENAI_PRODUCT_SEARCH_MODEL",
    "OPENAI_IMAGE_MODEL",
    "IMAGE_QUALITY",
    "IMAGE_SIZE",
    "DATABASE_URL",
    "SESSION_SECRET",
    "SESSION_COOKIE_SECURE",
    "OPENAI_TIMEOUT_TEXT",
    "OPENAI_TIMEOUT_IMAGE",
)


def _settings(**overrides) -> Settings:
    values = {
        "openai_api_key": "sk-test-dummy",
        "openai_text_model_primary": "gpt-5.4-nano",
        "openai_text_model_fallback": "gpt-5.4-mini",
        "openai_product_search_model": "gpt-5.4-nano",
        "openai_image_model": "gpt-image-2",
        "image_quality": "low",
        "image_size": "1024x1024",
        "database_url": "sqlite:///outfit.db",
        "session_secret": "test-session-secret",
        "session_cookie_secure": False,
        "openai_timeout_text": 60.0,
        "openai_timeout_image": 120.0,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_settings_have_safe_project_defaults(monkeypatch):
    for variable in PROJECT_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None, openai_api_key="sk-test-dummy")

    assert settings.openai_text_model_primary == "gpt-5.4-nano"
    assert settings.openai_text_model_fallback == "gpt-5.4-mini"
    assert settings.openai_product_search_model == "gpt-5.4-nano"
    assert settings.openai_image_model == "gpt-image-2"
    assert settings.image_quality == "low"
    assert settings.image_size == "1024x1024"
    assert settings.database_url == "sqlite:///outfit.db"
    assert settings.session_secret.get_secret_value() == (
        "outfit-mvp-local-session-secret-change-before-sharing"
    )
    assert settings.session_cookie_secure is False
    assert settings.openai_timeout_text == 60.0
    assert settings.openai_timeout_image == 120.0


def test_settings_read_environment_variables(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL_PRIMARY", "primary-from-env")
    monkeypatch.setenv("OPENAI_TEXT_MODEL_FALLBACK", "fallback-from-env")
    monkeypatch.setenv("OPENAI_TIMEOUT_TEXT", "15.5")

    settings = Settings(_env_file=None)

    assert settings.openai_text_model_primary == "primary-from-env"
    assert settings.openai_text_model_fallback == "fallback-from-env"
    assert settings.openai_timeout_text == 15.5


def test_settings_read_dotenv_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("IMAGE_QUALITY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("IMAGE_QUALITY=medium\nOPENAI_API_KEY=from-dotenv\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.image_quality == "medium"
    assert settings.require_openai_api_key() == "from-dotenv"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("openai_timeout_text", 0),
        ("openai_timeout_image", -1),
        ("openai_product_search_model", "unverified-search-model"),
        ("openai_image_model", "unverified-image-model"),
        ("image_quality", "ultra"),
        ("image_size", "1024x1536"),
        ("database_url", "not a database url"),
    ],
)
def test_invalid_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_primary_and_fallback_models_must_be_distinct():
    with pytest.raises(ValidationError, match="deben ser distintos"):
        _settings(
            openai_text_model_primary="same-model",
            openai_text_model_fallback="same-model",
        )


def test_startup_requires_a_non_empty_api_key():
    settings = _settings(openai_api_key="  ")

    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        settings.validate_for_startup()


def test_api_key_is_not_exposed_in_settings_repr():
    settings = _settings(openai_api_key="sk-super-secret")

    assert "sk-super-secret" not in repr(settings)
