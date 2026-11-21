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


PAGINA_TESTO = """<html><body><header>Menu del sito</header><form id="aspnet"><main>
<h1>Voucher turismo 2026</h1>
<p>Beneficiari: micro e piccole imprese con sede in provincia, codici ATECO 55 e 56.</p>
<p>Spese ammissibili: arredi, attrezzature e siti web. Contributo a fondo perduto del 50%,
fino a 5.000 euro, in regime de minimis.</p>
<button>Stampa</button>
</main></form><footer>Note legali</footer></body></html>"""


def test_testo_della_pagina_per_l_ai_anche_senza_pdf(db, monkeypatch):
    """Il PDF non si scarica (robots.txt): l'AI legge comunque la pagina ufficiale. Un link
    «documento» che porta a una pagina web viene letto come pagina (21/11/2026)."""
    from app.documenti import servizio
    from app.documenti.servizio import elabora, testo_per_ai
    from app.sources import http

    monkeypatch.setattr(http, "INTERVALLO_MINIMO_SECONDI", 0)
    assert pagina.pagina_del_bando("https://www.irpiniasannio.camcom.it/bando-voucher-2026")
    assert not pagina.pagina_del_bando("https://www.regione.abruzzo.it/")
    assert not pagina.pagina_del_bando("https://www.ente.it/index.html")
    testo = pagina.testo_della_pagina(PAGINA_TESTO)
    assert "Spese ammissibili" in testo and "Menu del sito" not in testo
    assert "Stampa" not in testo and "Note legali" not in testo

    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    base = f"https://camera.example/{uuid.uuid4().hex}"
    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Voucher turismo",
              stato="aperto", raw={}, hash_contenuto="t" * 64, link_ufficiale=f"{base}/voucher")  # fmt: skip
    db.add(b)
    db.commit()

    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /riservato/\n")
        return httpx.Response(200, text=PAGINA_TESTO, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        pagina.leggi_pagina(db, b, c, Robots(c))
        vietato = DocumentoBando(bando_id=b.id, url="https://vietato.example/riservato/bando.pdf",
                                 titolo="Bando", tentativi=0)  # fmt: skip
        web = DocumentoBando(bando_id=b.id, url=f"{base}/dettaglio", titolo="Dettaglio",
                             tentativi=0)  # fmt: skip
        db.add_all([vietato, web])
        db.flush()
        assert not elabora(db, c, Robots(c), vietato)
        assert elabora(db, c, Robots(c), web)
    db.commit()
    assert "regime de minimis" in b.testo_pagina
    assert web.mime == "text/html" and web.errore is None and "fondo perduto" in web.testo_estratto
    assert vietato.errore == servizio.VIETATO_ROBOTS
    per_ai = testo_per_ai(db, b.id, 6000)
    assert "PAGINA UFFICIALE DEL BANDO" in per_ai and "DOCUMENTO (pagina web)" in per_ai
    assert "NON LETTI" in per_ai and "riservato/bando.pdf" in per_ai
    # i documenti letti come pagina non finiscono tra i «PDF da caricare»
    assert web.id not in {d.id for _, d in servizio.da_caricare(db)}


def test_cambiamenti_minimi_della_pagina_non_rimandano_all_ai():
    """Molti siti cambiano a ogni visita voti, date di aggiornamento o codici casuali: il
    bando non deve tornare all'AI (spesa inutile). Proroghe, date e importi sì."""
    from app.documenti.pagina import cambiamento_sostanziale as cambiato

    base = "\n".join(
        [
            "Voucher digitali 2026",
            "Beneficiari: micro e piccole imprese della provincia.",
            "Spese ammissibili: software, hardware e consulenze digitali.",
            "Contributo del 50% fino a 10.000 euro.",
            "Le domande si presentano dal 1/3/2026 al 30/11/2026.",
        ]
        + [f"Paragrafo descrittivo numero {i} del bando con dettagli." for i in range(20)]
    )
    assert cambiato(None, base)
    rumore = [
        "Ultimo aggiornamento: 21/11/2026",
        "Average:\n3.4\nvotes)",
        "dca775ab-d3ou-44f0-9f4f-603ce97faaf6",
        "Condividi su Linkedin",
    ]
    for extra in rumore:
        assert not cambiato(base, base + "\n" + extra), extra
    # cambiamenti che contano
    assert cambiato(base, base + "\nScadenza prorogata al 31/12/2026.")
    assert cambiato(base, base + "\nSportello sospeso per esaurimento fondi")
    assert cambiato(base, base.replace("30/11/2026", "15/12/2026"))
    assert cambiato(base, base.replace("fino a 10.000 euro", "fino a 20.000 euro"))
    riscritto = base + "\n" + "\n".join(f"Nuova sezione {i} con testo diverso." for i in range(15))
    assert cambiato(base, riscritto)


def test_pagine_con_pdf_si_rileggono_ogni_30_giorni(db):
    from datetime import UTC, datetime

    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pagina")) or Fonte(
        codice="prova_pagina", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    venti_giorni_fa = datetime.now(UTC) - timedelta(days=20)

    def bando(con_pdf: bool) -> Bando:
        b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando riletto",
                  stato="aperto", raw={}, hash_contenuto="r" * 64,
                  link_ufficiale=f"https://ente.example/{uuid.uuid4().hex}",
                  pagina_letta_il=venti_giorni_fa)  # fmt: skip
        db.add(b)
        db.flush()
        if con_pdf:
            db.add(DocumentoBando(bando_id=b.id, url=f"{b.link_ufficiale}.pdf", tentativi=1,
                                  testo_estratto="Testo del bando"))  # fmt: skip
        return b

    senza, con = bando(False), bando(True)
    db.commit()
    da_leggere = {x.id for x in pagina.da_leggere(db, limite=10_000)}
    assert senza.id in da_leggere and con.id not in da_leggere
