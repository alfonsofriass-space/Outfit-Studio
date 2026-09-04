from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.pricing import estimate_image_cost, estimate_text_cost


class ConfigurationError(RuntimeError):
    """La aplicación no puede arrancar con la configuración recibida."""


class Settings(BaseSettings):
    """Configuración única del backend, cargada desde entorno y .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr | None = None
    openai_text_model_primary: str = "gpt-5.4-nano"
    openai_text_model_fallback: str = "gpt-5.4-mini"
    # Separados de los modelos de extracción: proponer es una tarea distinta de leer,
    # su calidad se decide con un smoke propio, y solo esta vía persiste un coste
    # medido, así que solo ella necesita tarifas verificadas para poder arrancar.
    openai_proposal_model: str = "gpt-5.4-nano"
    openai_proposal_fallback_model: str = "gpt-5.4-mini"
    openai_product_search_model: Literal["gpt-5.4-nano"] = "gpt-5.4-nano"
    openai_image_model: Literal["gpt-image-2"] = "gpt-image-2"
    image_quality: Literal["low", "medium", "high"] = "low"
    image_size: Literal["1024x1024"] = "1024x1024"
    database_url: str = "sqlite:///outfit.db"
    session_secret: SecretStr = SecretStr("outfit-mvp-local-session-secret-change-before-sharing")
    session_cookie_secure: bool = False
    openai_timeout_text: float = Field(default=60.0, gt=0)
    openai_timeout_image: float = Field(default=120.0, gt=0)

    @field_validator(
        "openai_text_model_primary",
        "openai_text_model_fallback",
        "openai_proposal_model",
        "openai_proposal_fallback_model",
        "database_url",
        mode="before",
    )
    @classmethod
    def _strip_required_strings(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError(f"{info.field_name} no puede estar vacío")
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        try:
            make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL no es una URL de SQLAlchemy válida") from exc
        return value

    @model_validator(mode="after")
    def _validate_model_and_image_configuration(self) -> "Settings":
        if self.openai_text_model_primary == self.openai_text_model_fallback:
            raise ValueError("Los modelos de texto principal y fallback deben ser distintos")

        # Mantiene la misma barrera de coste que la generación: toda combinación
        # configurable debe tener una tarifa verificada antes de poder arrancar.
        estimate_image_cost(self.image_quality, self.image_size)

        if self.openai_proposal_model == self.openai_proposal_fallback_model:
            raise ValueError("Los modelos de propuestas principal y fallback deben ser distintos")

        # La vía de propuestas persiste su coste medido, así que sus dos modelos
        # posibles necesitan tarifa verificada antes de arrancar. Parar aquí es
        # preferible a descubrirlo con una llamada ya pagada en la mano.
        for model in (self.openai_proposal_model, self.openai_proposal_fallback_model):
            estimate_text_cost(model, input_tokens=0, output_tokens=0)
        return self

    def require_openai_api_key(self) -> str:
        value = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
        if not value.strip():
            raise ConfigurationError(
                "Falta OPENAI_API_KEY. Configúrala en el entorno o en .env antes de arrancar."
            )
        return value

    def validate_for_startup(self) -> None:
        self.require_openai_api_key()


@lru_cache
def get_settings() -> Settings:
    return Settings()
