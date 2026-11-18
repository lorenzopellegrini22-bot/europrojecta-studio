"""Tutti i modelli: importarli qui li registra su Base.metadata (serve ad Alembic e ai test)."""

from app.models.auth import AuditLog, CodiceRecupero, LogAccesso, Sessione, Utente
from app.models.bandi import Bando, BandoRequisiti, BandoVersione, Fonte, FonteRun
from app.models.impostazioni import Integrazione
from app.models.jobs import JobRun

__all__ = [
    "AuditLog",
    "Bando",
    "BandoRequisiti",
    "BandoVersione",
    "CodiceRecupero",
    "Fonte",
    "FonteRun",
    "Integrazione",
    "JobRun",
    "LogAccesso",
    "Sessione",
    "Utente",
]
