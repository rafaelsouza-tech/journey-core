"""
Configuração da aplicação via variáveis de ambiente (pydantic-settings).

Só o que o núcleo precisa: ambiente, salt do hash de telefone, caminhos dos
artefatos declarativos (templates, planos, regras) e logging. O cooldown de
follow-up NÃO é configuração — mora nas regras declarativas (fonte única).
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent


class AppEnv(StrEnum):
    """Ambientes de execução reconhecidos."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Configurações carregadas de variáveis de ambiente e do arquivo `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    APP_NAME: str = "journey-core"
    APP_ENV: AppEnv = AppEnv.DEVELOPMENT
    API_VERSION: str = "1.0.0"

    # PII: o telefone é persistido como HMAC-SHA256 com este salt. Sem default
    # proposital — um salt esquecido em produção seria um incidente, não um aviso.
    PHONE_HASH_SALT: str = Field(min_length=16)

    # Artefatos declarativos (data-driven). Trocar o JSON/YAML não exige código.
    PROTOCOL_TEMPLATES_DIR: Path = APP_DIR / "features" / "protocols" / "templates"
    JOURNEY_PLANS_DIR: Path = APP_DIR / "features" / "journeys" / "plans"
    FOLLOWUP_RULES_PATH: Path = APP_DIR / "features" / "followups" / "rules" / "default.yaml"

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"
    DOCS_ENABLED: bool = True

    @property
    def is_production(self) -> bool:
        """Indica se a aplicação está em produção."""
        return self.APP_ENV is AppEnv.PRODUCTION


def load_settings() -> Settings:
    """
    Carrega as configurações com mensagem de erro amigável quando algo obrigatório falta.

    Raises:
        RuntimeError: se uma variável obrigatória (ex.: PHONE_HASH_SALT) estiver ausente/inválida.
    """
    try:
        return Settings()  # campos obrigatórios vêm do ambiente
    except ValidationError as exc:
        missing = ", ".join(str(err["loc"][0]) for err in exc.errors())
        raise RuntimeError(
            f"Configuração inválida ({missing}). "
            "Copie .env.example para .env e defina PHONE_HASH_SALT "
            "(ex.: `openssl rand -hex 32`) — ou rode `make setup`."
        ) from exc
