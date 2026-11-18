"""Processo worker: esegue i job pianificati con APScheduler.

Job:
- raccolta_bandi: ogni notte all'ora RACCOLTA_ORARIO (default 02:30), da tutte le fonti;
  parte anche all'avvio se non è mai riuscita, così una nuova installazione ha subito i dati;
- pulizia: ogni notte all'ora PULIZIA_ORARIO (default 04:00);
- promemoria: ogni mattina all'ora PROMEMORIA_ORARIO (default 07:30), email delle scadenze;
- raccolta_appalti: ogni notte all'ora APPALTI_ORARIO (default 03:15), TED e Consip;
- documenti_bandi: ogni notte all'ora DOCUMENTI_ORARIO (default 04:05), PDF dei bandi;
- estrazione_bandi: ogni notte all'ora ESTRAZIONE_ORARIO (default 04:30), requisiti con l'AI;
- opportunita: ogni mattina all'ora OPPORTUNITA_ORARIO (default 07:00), abbinamento clienti
  ↔ bandi/appalti ed email con le novità;
- battito: ogni 15 minuti verifica il database (compare nei log).

Ogni job gira sotto un advisory lock di Postgres e ogni esecuzione è registrata in job_run.
Se il worker era spento all'ora prevista, il job parte appena riavviato (entro 6 ore).
"""

import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, text

from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.jobs import attivita
from app.jobs.esecuzione import esegui_job
from app.models import Fonte

log = logging.getLogger("worker")

TOLLERANZA_RITARDO_SECONDI = 6 * 3600


def battito() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    log.info("battito: database raggiungibile")


def istat_mancante() -> bool:
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Comune

    with get_sessionmaker()() as db:
        return db.scalar(select(Comune.codice_istat).limit(1)) is None


def job_raccolta() -> None:
    esegui_job("raccolta_bandi", attivita.raccolta_bandi)


def job_pulizia() -> None:
    esegui_job("pulizia", attivita.pulizia)


def job_documenti() -> None:
    esegui_job("documenti_bandi", attivita.documenti_bandi)


def job_estrazione() -> None:
    esegui_job("estrazione_bandi", attivita.estrazione_bandi)


def job_opportunita() -> None:
    esegui_job("opportunita", attivita.opportunita)


def job_appalti() -> None:
    esegui_job("raccolta_appalti", attivita.raccolta_appalti)


def job_istat() -> None:
    esegui_job("riferimenti_istat", attivita.riferimenti_istat)


def job_promemoria() -> None:
    esegui_job("promemoria", attivita.promemoria_scadenze)


def _cron(orario: str, fuso: str) -> CronTrigger:
    ore, minuti = orario.split(":")
    return CronTrigger(hour=int(ore), minute=int(minuti), timezone=fuso)


def raccolta_mai_riuscita() -> bool:
    with get_sessionmaker()() as db:
        return db.scalar(select(Fonte.id).where(Fonte.ultimo_successo.is_not(None))) is None


def crea_scheduler() -> BlockingScheduler:
    settings = get_settings()
    fuso = settings.fuso_orario
    scheduler = BlockingScheduler(
        timezone=fuso,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": TOLLERANZA_RITARDO_SECONDI,
        },
    )
    notturno = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": TOLLERANZA_RITARDO_SECONDI,
    }
    scheduler.add_job(
        job_raccolta, _cron(settings.raccolta_orario, fuso), id="raccolta_bandi", **notturno
    )
    scheduler.add_job(job_pulizia, _cron(settings.pulizia_orario, fuso), id="pulizia", **notturno)
    scheduler.add_job(
        job_appalti, _cron(settings.appalti_orario, fuso), id="raccolta_appalti", **notturno
    )
    scheduler.add_job(
        job_documenti,
        _cron(settings.documenti_orario, fuso),
        id="documenti_bandi",
        **notturno,
    )
    scheduler.add_job(
        job_estrazione,
        _cron(settings.estrazione_orario, fuso),
        id="estrazione_bandi",
        **notturno,
    )
    scheduler.add_job(
        job_opportunita,
        _cron(settings.opportunita_orario, fuso),
        id="opportunita",
        **notturno,
    )
    scheduler.add_job(
        job_promemoria, _cron(settings.promemoria_orario, fuso), id="promemoria", **notturno
    )
    # Tabelle ISTAT (comuni, raccordo ATECO): il primo giorno del mese
    scheduler.add_job(
        job_istat,
        CronTrigger(day=1, hour=3, minute=40, timezone=fuso),
        id="riferimenti_istat",
        **notturno,
    )
    scheduler.add_job(battito, "interval", minutes=15, id="battito")
    return scheduler


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = crea_scheduler()
    if raccolta_mai_riuscita():
        log.info("Prima raccolta mai eseguita: la avvio subito")
        scheduler.add_job(job_raccolta, "date", run_date=datetime.now(), id="raccolta_iniziale")
    if istat_mancante():
        log.info("Tabelle ISTAT vuote: le carico subito")
        scheduler.add_job(job_istat, "date", run_date=datetime.now(), id="istat_iniziale")
    for job in scheduler.get_jobs():
        log.info("Job pianificato: %s (%s)", job.id, job.trigger)
    battito()
    scheduler.start()


if __name__ == "__main__":
    main()
