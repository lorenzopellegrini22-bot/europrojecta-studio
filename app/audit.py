"""Registro delle operazioni (audit_log)."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Utente


def registra(
    db: Session,
    utente: Utente | None,
    entita: str,
    entita_id: str | int | None,
    azione: str,
    differenze: dict[str, Any] | None = None,
) -> None:
    """Aggiunge una riga all'audit. Il commit lo fa il chiamante, insieme alla modifica."""
    db.add(
        AuditLog(
            utente_id=utente.id if utente else None,
            entita=entita,
            entita_id=str(entita_id) if entita_id is not None else None,
            azione=azione,
            differenze=differenze,
        )
    )
