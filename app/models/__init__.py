"""Tutti i modelli: importarli qui li registra su Base.metadata (serve ad Alembic e ai test)."""

from app.models.auth import AuditLog, CodiceRecupero, LogAccesso, Sessione, Utente
from app.models.impostazioni import Integrazione

__all__ = ["AuditLog", "CodiceRecupero", "Integrazione", "LogAccesso", "Sessione", "Utente"]
