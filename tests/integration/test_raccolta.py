"""Raccolta dei bandi su database reale, con una fonte finta basata sul campione reale."""

import contextlib
import copy
import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Bando, BandoVersione, Fonte, FonteRun, JobRun
from app.sources.incentivi_italia import FONTE as INCENTIVI
from app.sources.runner import esegui_raccolta
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration

CAMPIONE = Path(__file__).parents[1] / "fixtures" / "incentivi_italia_campione.json"
OGGI = date(2026, 11, 18)


class FonteFinta:
    """Stessa interfaccia di una fonte vera; il "file remoto" lo decide il test."""

    def __init__(self, tmp_path: Path):
        self.codice = f"prova_{uuid.uuid4().hex[:8]}"
        self.nome = "Fonte di prova"
        self.url = "https://esempio.test/bandi.json"
        self.versione = "v1"
        self.records = json.loads(CAMPIONE.read_text(encoding="utf-8"))
        self.errore: Exception | None = None
        self._tmp = tmp_path

    def versione_remota(self, _client):
        return self.versione

    def scarica(self, _client) -> Path:
        if self.errore:
            raise self.errore
        p = self._tmp / f"{uuid.uuid4().hex}.json"
        p.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        return p

    def interpreta(self, percorso: Path):
        return INCENTIVI.interpreta(percorso)


def _esegui(db, fonte: FonteFinta, **kw) -> FonteRun:
    return esegui_raccolta(
        db, fonte, oggi=OGGI, fabbrica_client=lambda: contextlib.nullcontext(), **kw
    )


def _bandi(db, fonte: FonteFinta) -> list[Bando]:
    f = db.scalar(select(Fonte).where(Fonte.codice == fonte.codice))
    return list(db.scalars(select(Bando).where(Bando.fonte_id == f.id)))


def test_prima_raccolta_importa_tutto(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    run = _esegui(db, fonte)
    assert run.esito == "ok", run.messaggio
    assert (run.record_letti, run.nuovi) == (50, 50)
    bandi = _bandi(db, fonte)
    assert len(bandi) == 50
    assert all(b.requisiti is not None for b in bandi)
    stati = {b.stato for b in bandi}
    assert {"aperto", "chiuso", "in_arrivo"} <= stati


def test_file_invariato_non_si_riscarica(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    _esegui(db, fonte)
    fonte.errore = RuntimeError("non dovrebbe scaricare")
    run = _esegui(db, fonte)
    assert run.esito == "invariato"


def test_modifica_salva_la_versione_precedente(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    _esegui(db, fonte)
    vecchio_titolo = fonte.records[0]["Titolo"]
    fonte.records[0]["Titolo"] = vecchio_titolo + " (rettificato)"
    fonte.versione = "v2"
    run = _esegui(db, fonte)
    assert (run.modificati, run.invariati, run.nuovi) == (1, 49, 0)
    bando = next(b for b in _bandi(db, fonte) if b.id_esterno == fonte.records[0]["ID_Incentivo"])
    assert bando.titolo.endswith("(rettificato)")
    versione = db.scalar(select(BandoVersione).where(BandoVersione.bando_id == bando.id))
    assert versione.raw["Titolo"] == vecchio_titolo


def test_bando_scomparso_per_due_versioni_viene_chiuso(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    # un bando aperto senza data di chiusura
    indice = next(
        i
        for i, r in enumerate(fonte.records)
        if r.get("Data_apertura") and not r.get("Data_chiusura")
    )
    id_sparito = fonte.records[indice]["ID_Incentivo"]
    _esegui(db, fonte)
    del fonte.records[indice]

    fonte.versione = "v2"
    _esegui(db, fonte)
    bando = next(b for b in _bandi(db, fonte) if b.id_esterno == id_sparito)
    assert bando.stato == "aperto" and bando.versioni_assenti == 1  # una sola assenza: non basta

    fonte.versione = "v3"
    run = _esegui(db, fonte)
    db.refresh(bando)
    assert bando.stato == "chiuso" and bando.scomparso_dal_feed_il is not None
    assert run.scomparsi == 1


def test_controllo_sanita_ferma_il_run_senza_toccare_i_bandi(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    _esegui(db, fonte)
    fonte.records = fonte.records[:30]  # 60% dei record: download sospetto
    fonte.versione = "v2"
    run = _esegui(db, fonte)
    assert run.esito == "fermato_sanita"
    assert all(b.versioni_assenti == 0 for b in _bandi(db, fonte))
    f = db.scalar(select(Fonte).where(Fonte.codice == fonte.codice))
    assert f.fallimenti_consecutivi == 1
    assert f.ultimo_last_modified == "v1"  # la versione sospetta non è considerata elaborata


def test_errore_registrato_senza_crash(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    fonte.errore = ConnectionError("server irraggiungibile")
    run = _esegui(db, fonte)
    assert run.esito == "errore"
    assert "server irraggiungibile" in run.messaggio
    run = _esegui(db, fonte)
    f = db.scalar(select(Fonte).where(Fonte.codice == fonte.codice))
    assert f.fallimenti_consecutivi == 2


def test_id_reimpostato_riconciliato_con_id_rna(db, tmp_path):
    fonte = FonteFinta(tmp_path)
    _esegui(db, fonte)
    vecchio_id = fonte.records[0]["ID_Incentivo"]
    fonte.records = copy.deepcopy(fonte.records)
    fonte.records[0]["ID_Incentivo"] = "NUOVO-" + vecchio_id
    fonte.versione = "v2"
    run = _esegui(db, fonte)
    assert run.nuovi == 0 and run.modificati == 1
    assert run.dettagli["riconciliati_per_id_rna"] == 1
    assert len(_bandi(db, fonte)) == 50


def test_raccolta_dal_job_registrata(db, monkeypatch, tmp_path):
    from app.jobs import attivita
    from app.jobs.esecuzione import esegui_job

    fonte = FonteFinta(tmp_path)
    monkeypatch.setattr(attivita, "FONTI", {fonte.codice: fonte})
    monkeypatch.setattr(
        attivita,
        "esegui_raccolta",
        lambda db_, impl, forza=False: _esegui(db_, impl, forza=forza),
    )
    assert esegui_job("raccolta_bandi", attivita.raccolta_bandi) == "ok"
    fonte.errore = ConnectionError("giù")
    fonte.versione = "v2"
    assert esegui_job("raccolta_bandi", attivita.raccolta_bandi) == "errore"
    ultimi = db.scalars(
        select(JobRun.esito).where(JobRun.job == "raccolta_bandi").order_by(JobRun.id.desc())
    ).all()
    assert ultimi[:2] == ["errore", "ok"]


def test_pagine_bandi(client, crea_utente, accedi, db, tmp_path):
    fonte = FonteFinta(tmp_path)
    _esegui(db, fonte)
    accedi(crea_utente())

    r = client.get("/bandi?stato=tutti")
    assert r.status_code == 200
    totale = db.scalar(select(func.count()).select_from(Bando))
    assert f"{totale} bandi trovati" in r.text

    r = client.get(
        "/bandi?stato=tutti&regione=Lazio&forma=Contributo/Fondo perduto&scadenza=30&q=a"
    )
    assert r.status_code == 200

    bando = _bandi(db, fonte)[0]
    r = client.get(f"/bandi/{bando.id}")
    assert r.status_code == 200
    assert "CC BY 4.0" in r.text
    assert client.get("/bandi/999999999").status_code == 404

    r = client.get("/fonti")
    assert r.status_code == 200 and "02:30" in r.text


def test_collaboratore_non_avvia_la_raccolta(client, crea_utente, accedi):
    accedi(crea_utente(ruolo="collaboratore"))
    r = client.post("/fonti/raccogli", data={"csrf_token": csrf(client, "/fonti")})
    assert r.status_code == 403
