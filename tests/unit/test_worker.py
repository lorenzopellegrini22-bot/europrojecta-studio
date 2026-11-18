"""La raccolta dei bandi deve essere pianificata ogni notte (requisito del cliente)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.jobs.worker import TOLLERANZA_RITARDO_SECONDI, crea_scheduler

ROMA = ZoneInfo("Europe/Rome")


@pytest.fixture
def scheduler():
    s = crea_scheduler()
    yield s
    get_settings.cache_clear()


def _job(s, id_):
    return next(j for j in s.get_jobs() if j.id == id_)


def test_raccolta_pianificata_ogni_notte_alle_2_30(scheduler):
    job = _job(scheduler, "raccolta_bandi")
    prossima = job.trigger.get_next_fire_time(None, datetime(2026, 11, 18, 10, 0, tzinfo=ROMA))
    assert prossima == datetime(2026, 11, 19, 2, 30, tzinfo=ROMA)
    # e la notte dopo ancora
    dopo = job.trigger.get_next_fire_time(prossima, prossima.replace(minute=31))
    assert dopo == datetime(2026, 11, 20, 2, 30, tzinfo=ROMA)


def test_recupera_se_il_server_era_spento_e_non_si_sovrappone(scheduler):
    job = _job(scheduler, "raccolta_bandi")
    assert job.misfire_grace_time == TOLLERANZA_RITARDO_SECONDI
    assert job.coalesce is True
    assert job.max_instances == 1


def test_orario_configurabile(monkeypatch):
    monkeypatch.setenv("RACCOLTA_ORARIO", "05:15")
    get_settings.cache_clear()
    try:
        s = crea_scheduler()
        job = _job(s, "raccolta_bandi")
        prossima = job.trigger.get_next_fire_time(None, datetime(2026, 11, 18, 10, 0, tzinfo=ROMA))
        assert prossima == datetime(2026, 11, 19, 5, 15, tzinfo=ROMA)
    finally:
        get_settings.cache_clear()


def test_pulizia_pianificata(scheduler):
    assert _job(scheduler, "pulizia")
