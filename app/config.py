"""Configurazione letta dalle variabili d'ambiente (o da un file .env in sviluppo).

Nessun segreto sta nel repository: vedi .env.example per l'elenco delle variabili.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: Literal["sviluppo", "test", "produzione"] = "sviluppo"

    # URL SQLAlchemy, driver psycopg 3: postgresql+psycopg://utente:password@host:5432/db
    database_url: str = Field(min_length=1)

    # Chiave da cui si derivano le chiavi di cifratura (API key OpenAI, segreti TOTP).
    # Almeno 32 caratteri casuali: python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: SecretStr = Field(min_length=32)
    # Rotazione: le chiavi vecchie, separate da virgola. Servono solo a leggere i segreti
    # cifrati prima del cambio, finché non si lancia `python -m app.cli ruota-chiavi`.
    secret_key_precedenti: SecretStr | None = None

    log_level: str = "INFO"

    # Indirizzo pubblico dell'app (link nelle email: disiscrizione, schede). In produzione
    # https://<dominio>.
    url_pubblico: str = "http://localhost:8000"

    # Fuso orario per i job pianificati (raccolta notturna, email del mattino).
    fuso_orario: str = "Europe/Rome"

    # Orari dei job notturni (HH:MM, nel fuso orario sopra)
    raccolta_orario: str = Field(default="02:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    pulizia_orario: str = Field(default="04:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    promemoria_orario: str = Field(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    appalti_orario: str = Field(default="03:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    opportunita_orario: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    estrazione_orario: str = Field(default="04:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    documenti_orario: str = Field(default="04:05", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Allegati delle pratiche: cartella sul server (volume Docker in produzione) e limite
    cartella_allegati: str = "dati/allegati"
    # Dove stanno i file caricati: "disco" (cartella sopra) o "database" (tabella
    # allegato_contenuto), per le piattaforme senza disco permanente come Vercel.
    archivio_allegati: Literal["disco", "database"] = "disco"
    allegato_max_mb: int = 20
    # Antivirus ClamAV per i file caricati (vuoto = nessun controllo, solo in sviluppo)
    clamav_host: str | None = None
    clamav_porta: int = 3310
    # Job notturni via HTTP (Vercel Cron, app/jobs/cron.py): vuoto = rotte /cron spente
    cron_secret: SecretStr | None = None
    # Avviso email quando una fonte dei bandi fallisce N raccolte di seguito
    avviso_fonte_dopo: int = Field(default=2, ge=1)

    # Massimali de minimis in euro su 3 anni mobili (configurabili: le norme cambiano).
    # Generale: Reg. (UE) 2023/2831; SIEG: Reg. (UE) 2023/2832;
    # agricolo: Reg. (UE) 1408/2013 come modificato dal Reg. (UE) 2024/3118;
    # pesca: Reg. (UE) 2023/2391. Da ricontrollare con le norme vigenti.
    de_minimis_generale: int = 300_000
    de_minimis_sieg: int = 750_000
    de_minimis_agricolo: int = 50_000
    de_minimis_pesca: int = 40_000

    @field_validator("database_url")
    @classmethod
    def _driver_psycopg(cls, valore: str) -> str:
        """I servizi gestiti (Neon su Vercel, Heroku...) danno postgres:// o postgresql://:
        si usa sempre il driver psycopg 3."""
        for prefisso in ("postgres://", "postgresql://"):
            if valore.startswith(prefisso):
                return "postgresql+psycopg://" + valore[len(prefisso) :]
        return valore

    # Sessioni e login
    # Verifica in due passaggi (app di autenticazione) obbligatoria. Si spegne solo per scelta
    # del titolare dell'installazione (VERIFICA_DUE_PASSAGGI=false): allora basta la password.
    verifica_due_passaggi: bool = True
    # Pagina /primo-accesso per creare il primo titolare dal browser (dove non c'è la riga
    # di comando, es. Vercel). Funziona solo finché non esiste nessun utente.
    primo_accesso: bool = False
    cookie_secure: bool = True
    sessione_inattivita_minuti: int = Field(default=60, ge=5)
    sessione_durata_max_ore: int = Field(default=12, ge=1)
    login_max_tentativi: int = Field(default=5, ge=1)
    login_blocco_minuti: int = Field(default=15, ge=1)
    # Tentativi falliti dallo stesso IP (su qualsiasi account) entro login_blocco_minuti
    login_max_tentativi_ip: int = Field(default=20, ge=5)


@lru_cache
def get_settings() -> Settings:
    """Settings in cache: si leggono una sola volta per processo."""
    return Settings()
