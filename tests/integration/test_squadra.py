# ruff: noqa: E501
"""Uso quotidiano: «Oggi», calendario personale, ricerca unica, app sul telefono, note con
@menzioni, bandi e gare seguiti."""

import random
import re
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Appalto,
    Azienda,
    Bando,
    DocumentoBando,
    Fonte,
    Menzione,
    NovitaSeguito,
    Utente,
)
from app.pratiche.servizio import crea_pratica
from app.squadra import seguiti
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _azienda(db) -> Azienda:
    a = Azienda(
        ragione_sociale=f"Squadra Prova {uuid.uuid4().hex[:6]} S.r.l.",
        partita_iva="".join(random.choices("0123456789", k=11)),
        stato_cliente="attivo",
    )
    db.add(a)
    db.flush()
    return a


def _bando(db, titolo="Bando squadra") -> Bando:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_squadra")) or Fonte(
        codice="prova_squadra", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    b = Bando(
        fonte_id=fonte.id,
        id_esterno=uuid.uuid4().hex,
        titolo=f"{titolo} {uuid.uuid4().hex[:6]}",
        stato="aperto",
        raw={},
        hash_contenuto="a" * 64,
        data_chiusura=date.today() + timedelta(days=40),
    )
    db.add(b)
    db.flush()
    return b


def _utente(crea_utente, db, nome: str, ruolo: str = "titolare", sezioni=None) -> Utente:
    u = crea_utente(ruolo)
    u.nome = nome
    if sezioni is not None:
        u.sezioni = sezioni
    db.commit()
    return u


def test_oggi_e_calendario(client, crea_utente, accedi, db):
    u = _utente(crea_utente, db, "Anna Bianchi")
    a = _azienda(db)
    p = crea_pratica(db, u, a, date.today(), bando_testo="Bando digitale", responsabile=u,
                     scadenza_presentazione=date.today() + timedelta(days=3))  # fmt: skip
    db.commit()
    accedi(u)
    pagina = client.get("/oggi").text
    assert (
        "Le mie pratiche" in pagina
        and p.codice in pagina
        and "Presentazione della domanda" in pagina
    )

    token = csrf(client, "/oggi")
    r = client.post("/oggi/calendario", data={"csrf_token": token})
    link = re.search(r'id="link-calendario">([^<]+)<', r.text).group(1)
    percorso = link.split("://", 1)[1].split("/", 1)[1]
    ics = client.get("/" + percorso)
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in ics.text and "Presentazione della domanda" in ics.text
    assert f"DTSTART;VALUE=DATE:{p.scadenza_presentazione:%Y%m%d}" in ics.text
    # un nuovo link invalida il vecchio
    client.post("/oggi/calendario", data={"csrf_token": token})
    client.cookies.clear()
    assert client.get("/" + percorso).status_code == 404
    assert client.get("/calendario/inventato.ics").status_code == 404


def test_ricerca_unica_e_permessi(client, crea_utente, accedi, db):
    u = _utente(crea_utente, db, "Carlo Verdi", "collaboratore", ["bandi"])
    a = _azienda(db)
    b = _bando(db, "Contributi digitalizzazione squadra")
    db.commit()
    accedi(u)
    # senza la sezione clienti la P.IVA non trova il cliente
    r = client.get(f"/cerca?q={a.partita_iva}")
    assert r.status_code == 200 and a.ragione_sociale not in r.text
    r = client.get(f"/cerca?q={b.titolo.split()[-1]}")
    assert r.status_code == 303 and r.headers["location"] == f"/bandi/{b.id}"
    assert "Cerca" in client.get("/cerca?q=x").text


def test_ricerca_per_partita_iva(client, crea_utente, accedi, db):
    u = _utente(crea_utente, db, "Dora Neri")
    a = _azienda(db)
    db.commit()
    accedi(u)
    r = client.get(f"/cerca?q={a.partita_iva}")
    assert r.status_code == 303 and r.headers["location"] == f"/clienti/{a.id}"
    assert 'role="search"' in client.get("/").text


def test_app_installabile(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.json()["display"] == "standalone"
    assert {i["sizes"] for i in r.json()["icons"]} >= {"192x192", "512x512"}
    assert client.get("/static/img/icona-512.png").status_code == 200


def test_note_con_menzioni(client, crea_utente, accedi, db):
    titolare = _utente(crea_utente, db, "Elena Gialli")
    collega = _utente(crea_utente, db, "Filippo Blu", "collaboratore")
    a = _azienda(db)
    p = crea_pratica(db, titolare, a, date.today(), bando_testo="Bando note")
    db.commit()
    accedi(titolare)
    token = csrf(client, f"/pratiche/{p.id}")
    r = client.post(
        f"/pratiche/{p.id}/note",
        data={"csrf_token": token, "testo": "@filippo chiama il cliente <b>domani</b>"},
    )
    assert r.status_code == 303
    pagina = client.get(f"/pratiche/{p.id}").text
    assert '<strong class="menzione">@filippo</strong>' in pagina and "&lt;b&gt;domani" in pagina
    m = db.scalar(select(Menzione).where(Menzione.utente_id == collega.id))
    assert m is not None and m.letta_il is None

    client.cookies.clear()
    accedi(collega)
    oggi = client.get("/oggi").text
    assert "Ti hanno citato" in oggi and "chiama il cliente" in oggi
    assert 'class="contatore"' in client.get("/").text
    client.get(f"/pratiche/{p.id}")  # aprendo la pratica la menzione è letta
    db.expire_all()
    assert db.get(Menzione, m.id).letta_il is not None
    # non si eliminano le note degli altri
    token = csrf(client, f"/pratiche/{p.id}")
    assert (
        client.post(
            f"/pratiche/{p.id}/note/{m.commento_id}/elimina", data={"csrf_token": token}
        ).status_code
        == 403
    )


def test_segui_bando(client, crea_utente, accedi, db):
    u = _utente(crea_utente, db, "Giulia Rosa")
    b = _bando(db)
    db.commit()
    accedi(u)
    token = csrf(client, f"/bandi/{b.id}")
    assert "☆ Segui" in client.get(f"/bandi/{b.id}").text
    assert client.post(f"/seguiti/bando/{b.id}", data={"csrf_token": token}).status_code == 303
    assert "★ Segui" in client.get(f"/bandi/{b.id}").text

    # la fonte sposta la scadenza e pubblica le FAQ
    b.data_chiusura = b.data_chiusura + timedelta(days=15)
    db.add(
        DocumentoBando(
            bando_id=b.id,
            url="https://esempio.test/faq.pdf",
            titolo="FAQ_aggiornate.pdf",
            tentativi=0,
        )
    )
    db.commit()
    assert "novità" in seguiti.controlla(db)
    oggi = client.get("/oggi").text
    assert "Scadenza cambiata" in oggi and "Nuovi documenti: FAQ aggiornate" in oggi
    assert "Novità da quando lo segui" in client.get(f"/bandi/{b.id}").text
    db.expire_all()
    assert all(n.letta_il for n in db.scalars(select(NovitaSeguito)) if n.seguito.bando_id == b.id)
    # nessun cambiamento: nessuna novità nuova
    seguiti.controlla(db)
    assert "Novità da quando lo segui" not in client.get(f"/bandi/{b.id}").text
    client.post(f"/seguiti/bando/{b.id}", data={"csrf_token": token, "azione": "smetti"})
    assert "☆ Segui" in client.get(f"/bandi/{b.id}").text


def test_segui_gara_documenti(db, crea_utente):
    u = _utente(crea_utente, db, "Ivo Viola")
    a = Appalto(fonte="ted", id_esterno=f"{random.randint(100000, 999999)}-2026", titolo="Gara seguita", raw={},
                scadenza=date.today() + timedelta(days=20), documenti=[])  # fmt: skip
    db.add(a)
    db.flush()
    seguiti.segui(db, u, gara=a)
    db.commit()
    a.documenti = [{"titolo": "Chiarimenti n. 1", "url": "https://p.example/chiarimenti1.pdf"}]
    db.commit()
    seguiti.controlla(db)
    testi = [n.testo for n, _t, _l in seguiti.novita(db, u)]
    assert testi == ["Nuovi documenti: Chiarimenti n. 1"]
