# ruff: noqa: E501
"""Controllo dei job notturni: worker silenzioso, job bloccati, falliti, che non partono."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.jobs import salute
from app.models import JobRun

pytestmark = pytest.mark.integration


@pytest.fixture
def registro_pulito(db):
    db.execute(delete(JobRun))
    db.commit()
    yield
    db.execute(delete(JobRun))
    db.commit()


def _run(db, job, ore_fa, esito="ok", messaggio=None):
    inizio = datetime.now(UTC) - timedelta(hours=ore_fa)
    db.add(
        JobRun(
            job=job,
            esito=esito,
            inizio=inizio,
            fine=None if esito == "in_corso" else inizio,
            messaggio=messaggio,
        )
    )
    db.commit()


def test_problemi_dei_job(db, registro_pulito):
    for job in salute.ATTESI:
        _run(db, job, 2)
    salute.registra_battito(db)
    assert salute.problemi(db) == []

    _run(db, "raccolta_bandi", 1, "errore", "ErroreRaccolta: incentivi_italia: errore — timeout")
    db.execute(delete(JobRun).where(JobRun.job == "estrazione_bandi"))
    _run(db, "estrazione_bandi", 4, "in_corso")
    db.execute(delete(JobRun).where(JobRun.job == "promemoria"))
    _run(db, "promemoria", 30)
    db.commit()
    testi = " | ".join(p.testo for p in salute.problemi(db))
    assert "Raccolta dei bandi: fallita" in testi and "timeout" in testi
    assert "Lettura dei bandi con l'AI: bloccata" in testi
    assert "Promemoria delle scadenze: non parte dal" in testi

    # worker fermo da un'ora
    ora = datetime.now(UTC) + timedelta(hours=1)
    assert any("non dà segni di vita" in p.testo for p in salute.problemi(db, ora))


def test_avviso_ai_titolari_una_volta(
    client, crea_utente, accedi, db, registro_pulito, monkeypatch
):
    from tests.integration.test_funzioni_studio import _configura_smtp

    titolare = crea_utente()
    accedi(titolare)
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)),
    )
    for job in salute.ATTESI:
        _run(db, job, 2)
    salute.registra_battito(db)
    _run(db, "raccolta_appalti", 1, "errore", "RuntimeError: TED non risponde")
    assert salute.avvisa(db) >= 1
    mie = [x for x in inviate if x[0] == titolare.email]
    assert mie and "TED non risponde" in mie[0][2]
    inviate.clear()
    salute.avvisa(db)
    assert not [x for x in inviate if x[0] == titolare.email]  # niente doppioni nello stesso giorno
    assert "Raccolta degli appalti: fallita" in client.get("/").text
