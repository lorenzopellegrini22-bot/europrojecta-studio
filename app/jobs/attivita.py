"""I job pianificati del worker. Ognuno si può lanciare anche a mano dalla riga di comando
(python -m app.cli raccogli-bandi / pulizia)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db import get_sessionmaker
from app.models import Fonte, LogAccesso, Sessione
from app.sources.registro import FONTI
from app.sources.runner import esegui_raccolta

CONSERVAZIONE_LOG_ACCESSI_GIORNI = 365


def raccolta_bandi(forza: bool = False, solo: str | None = None) -> str:
    """Raccoglie i bandi da tutte le fonti attive. Una fonte che fallisce non ferma le altre."""
    righe = []
    fallite = []
    with get_sessionmaker()() as db:
        disattivate = set(db.scalars(select(Fonte.codice).where(Fonte.attiva.is_(False))))
        for codice, impl in FONTI.items():
            if (solo and codice != solo) or codice in disattivate:
                continue
            run = esegui_raccolta(db, impl, forza=forza)
            righe.append(f"{codice}: {run.esito} — {run.messaggio or ''}")
            if run.esito in ("errore", "fermato_sanita"):
                fallite.append(codice)
    riepilogo = "\n".join(righe) or "Nessuna fonte attiva."
    if fallite:
        # Il job risulta in errore (e lo vede l'email del mattino), ma le altre fonti sono
        # state comunque elaborate.
        raise ErroreRaccolta(riepilogo)
    return riepilogo


class ErroreRaccolta(Exception):
    """Almeno una fonte non è stata raccolta correttamente."""


def pulizia() -> str:
    """Elimina le sessioni scadute e i log degli accessi più vecchi di un anno."""
    ora = datetime.now(UTC)
    with get_sessionmaker()() as db:
        sessioni = db.execute(delete(Sessione).where(Sessione.scade_il < ora)).rowcount
        log = db.execute(
            delete(LogAccesso).where(
                LogAccesso.creato_il < ora - timedelta(days=CONSERVAZIONE_LOG_ACCESSI_GIORNI)
            )
        ).rowcount
        db.commit()
    return f"Eliminate {sessioni} sessioni scadute e {log} log di accesso vecchi."
