"""Tutti i modelli: importarli qui li registra su Base.metadata (serve ad Alembic e ai test)."""

from app.models.auth import AuditLog, CodiceRecupero, LogAccesso, Sessione, Utente
from app.models.bandi import Bando, BandoRequisiti, BandoVersione, Fonte, FonteRun
from app.models.crm import Azienda, Referente, Sede
from app.models.impostazioni import Integrazione
from app.models.jobs import JobRun
from app.models.pratiche import (
    ModelloFase,
    ModelloOperazione,
    ModelloPratica,
    Pratica,
    PraticaDato,
    PraticaDocumento,
    PraticaEvento,
    PraticaFase,
    PraticaOperazione,
)

__all__ = [
    "AuditLog",
    "Azienda",
    "Bando",
    "BandoRequisiti",
    "BandoVersione",
    "CodiceRecupero",
    "Fonte",
    "FonteRun",
    "Integrazione",
    "JobRun",
    "LogAccesso",
    "ModelloFase",
    "ModelloOperazione",
    "ModelloPratica",
    "Pratica",
    "PraticaDato",
    "PraticaDocumento",
    "PraticaEvento",
    "PraticaFase",
    "PraticaOperazione",
    "Referente",
    "Sede",
    "Sessione",
    "Utente",
]
