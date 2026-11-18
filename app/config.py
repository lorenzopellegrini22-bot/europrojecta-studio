"""Configurazione letta dalle variabili d'ambiente (o da un file .env in sviluppo).

Nessun segreto sta nel repository: vedi .env.example per l'elenco delle variabili.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: Literal["sviluppo", "test", "produzione"] = "sviluppo"

    # URL SQLAlchemy, driver psycopg 3: postgresql+psycopg://utente:password@host:5432/db
    database_url: str = Field(min_length=1)

    # Chiave da cui si derivano le chiavi di cifratura (API key OpenAI, segreti TOTP).
    # Almeno 32 caratteri casuali: python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: SecretStr = Field(min_length=32)

    log_level: str = "INFO"

    # Fuso orario per i job pianificati (raccolta notturna, email del mattino).
    fuso_orario: str = "Europe/Rome"


@lru_cache
def get_settings() -> Settings:
    """Settings in cache: si leggono una sola volta per processo."""
    return Settings()
