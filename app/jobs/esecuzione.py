"""Esecuzione registrata dei job: advisory lock + riga in job_run per ogni esecuzione."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.db import get_engine, get_sessionmaker
from app.jobs.lock import lock_job
from app.models import JobRun

log = logging.getLogger("worker")


def esegui_job(nome: str, funzione: Callable[[], str]) -> str:
    """Esegue `funzione` sotto lock. Restituisce l'esito: ok, errore o saltato.

    `funzione` restituisce un messaggio di riepilogo; un'eccezione viene registrata e non
    ferma il worker (un errore in un job non blocca gli altri).
    """
    fabbrica = get_sessionmaker()
    with lock_job(get_engine(), nome) as preso, fabbrica() as db:
        run = JobRun(job=nome, esito="in_corso")
        db.add(run)
        db.commit()
        if not preso:
            run.esito, run.messaggio = "saltato", "Un'altra esecuzione era già in corso."
        else:
            try:
                run.messaggio = funzione()[:4000]
                run.esito = "ok"
            except Exception as e:  # noqa: BLE001
                log.exception("Job %s fallito", nome)
                run.esito, run.messaggio = "errore", f"{type(e).__name__}: {e}"[:4000]
        run.fine = datetime.now(UTC)
        db.commit()
        log.info("Job %s: %s — %s", nome, run.esito, run.messaggio)
        return run.esito
