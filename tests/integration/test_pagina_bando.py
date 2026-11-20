# ruff: noqa: E501
"""Pagina ufficiale del bando: i PDF trovati entrano nella Documentazione e nel passo dei PDF."""

import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.documenti import pagina
from app.documenti.servizio import Robots
from app.models import Bando, DocumentoBando, Fonte

pytestmark = pytest.mark.integration

HTML = """<html><body><main>
<a href="files/Avviso_pubblico.pdf">Avviso pubblico</a>
<a href="files/FAQ.pdf">FAQ aggiornate</a>
</main><footer><a href="/cookie.pdf">Cookie</a></footer></body></html>"""


def test_pdf_dalla_pagina_ufficiale(client, crea_utente, accedi, db, monkeypatch):
    from app.sources import http

    monkeypatch.setattr(http, "INTERVALLO_MINIMO_SECONDI", 0)
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    base = f"https://ente.example/{uuid.uuid4().hex}/bandi/avviso"
    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando con pagina",
              stato="aperto", raw={}, hash_contenuto="c" * 64, link_ufficiale=base,
              data_chiusura=date.today() + timedelta(days=5))  # fmt: skip
    db.add(b)
    db.commit()
    richieste = []

    def gestore(req: httpx.Request) -> httpx.Response:
        richieste.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        assert pagina.leggi_pagina(db, b, c, Robots(c)) == 2
    db.commit()
    assert base in richieste and not any(r.endswith(".pdf") for r in richieste)  # solo la pagina
    titoli = sorted(
        d.titolo for d in db.scalars(select(DocumentoBando).where(DocumentoBando.bando_id == b.id))
    )
    assert titoli == ["Avviso pubblico", "FAQ aggiornate"]
    # ora ha i PDF: non si rilegge la pagina
    assert b.id not in {x.id for x in pagina.da_leggere(db, limite=10_000)}

    accedi(crea_utente())
    scheda = client.get(f"/bandi/{b.id}").text
    assert f"{base.rsplit('/', 1)[0]}/files/Avviso_pubblico.pdf" in scheda


def test_pagina_letta_di_recente_non_si_rilegge(db):
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    from datetime import UTC, datetime

    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando letto", stato="aperto",
              raw={}, hash_contenuto="d" * 64, link_ufficiale="https://ente.example/a/b",
              pagina_letta_il=datetime.now(UTC))  # fmt: skip
    db.add(b)
    db.commit()
    assert b.id not in {x.id for x in pagina.da_leggere(db, limite=10_000)}


def test_un_pdf_che_rompe_non_ferma_gli_altri(db, monkeypatch):
    from app.documenti import servizio

    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando con PDF strani",
              stato="aperto", raw={}, hash_contenuto="e" * 64)  # fmt: skip
    db.add(b)
    db.flush()
    docs = [DocumentoBando(bando_id=b.id, url=f"https://ente.example/{uuid.uuid4().hex}/{i}.pdf",
                           titolo=f"Bando {i}", tentativi=0) for i in range(2)]  # fmt: skip
    db.add_all(docs)
    db.commit()
    rotto, buono = docs[0].id, docs[1].id
    visti = []

    def elabora_finto(db, client, robots, d, gia_letti, quota):
        visti.append(d.id)
        if d.id == rotto:
            d.testo_estratto = "testo con carattere nullo \x00"
            return True  # il salvataggio fallirà nel commit
        d.sha256 = "f" * 64
        return True

    monkeypatch.setattr(servizio, "elabora", elabora_finto)
    monkeypatch.setattr(servizio, "MAX_TENTATIVI", 10**6)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        servizio._scarica(db, c, limite=10**6)
    db.expire_all()
    assert rotto in visti and buono in visti
    assert db.get(DocumentoBando, rotto).errore.startswith("PDF non elaborato")
    assert db.get(DocumentoBando, buono).sha256 == "f" * 64


def test_testo_pdf_senza_caratteri_nulli(monkeypatch):
    from app.documenti import servizio
    from tests.unit.test_documenti import TESTO_BANDO, pdf_con_testo

    class Pagina:
        def extract_text(self):
            return "\n".join(TESTO_BANDO) + "\x00fine"

    class Lettore:
        def __init__(self, _f):
            self.pages = [Pagina()]

    monkeypatch.setattr("pypdf.PdfReader", Lettore)
    testo, _ = servizio.testo_pdf(pdf_con_testo("x"))
    assert "\x00" not in testo and "fine" in testo


def test_avviso_per_bandi_aperti_da_anni_senza_scadenza(client, crea_utente, accedi, db):
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()

    def bando(apertura):
        b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Sportello",
                  stato="aperto", raw={}, hash_contenuto="d" * 64, data_apertura=apertura)  # fmt: skip
        db.add(b)
        db.flush()
        return b

    vecchio, recente = bando(date(2005, 5, 8)), bando(date.today() - timedelta(days=200))
    db.commit()
    accedi(crea_utente())
    assert "Aperto dal 2005 senza una data di chiusura" in client.get(f"/bandi/{vecchio.id}").text
    assert "senza una data di chiusura" not in client.get(f"/bandi/{recente.id}").text
