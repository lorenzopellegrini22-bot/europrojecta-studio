"""Processo worker: esegue i job pianificati con APScheduler.

Fase 0: c'è solo un job "battito" che verifica il database ogni 15 minuti.
La pipeline notturna (raccolta, estrazione, matching) arriva in fase 5; ogni job
gira sotto un advisory lock e ogni esecuzione verrà registrata nel database.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.jobs.lock import lock_job

log = logging.getLogger("worker")


def battito() -> None:
    engine = get_engine()
    with lock_job(engine, "battito") as preso:
        if not preso:
            log.info("battito: già in esecuzione altrove, salto")
            return
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("battito: database raggiungibile")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = BlockingScheduler(timezone=settings.fuso_orario)
    scheduler.add_job(battito, "interval", minutes=15, id="battito", max_instances=1)
    log.info("Worker avviato (fuso orario %s)", settings.fuso_orario)
    battito()
    scheduler.start()


if __name__ == "__main__":
    main()
