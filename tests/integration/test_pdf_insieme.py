# ruff: noqa: E501
"""PDF che i siti vietano di scaricare: caricati tutti insieme e abbinati dal nome del file."""

import uuid

import pytest

from app.documenti import servizio as s
from app.models import Bando, Fonte
from app.models.documenti import DocumentoBando
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _pdf(testo: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((50, 80), testo, fontsize=10)
    return doc.tobytes()


def test_pdf_caricati_insieme(client, crea_utente, accedi, db):
    accedi(crea_utente())
    fonte = db.query(Fonte).filter(Fonte.codice == "prova_pdf").one_or_none() or Fonte(
        codice="prova_pdf", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    bandi = []
    for _ in range(2):
        b = Bando(
            fonte_id=fonte.id,
            id_esterno=uuid.uuid4().hex,
            titolo="Bando parco",
            stato="aperto",
            raw={},
            hash_contenuto="x" * 64,
        )
        db.add(b)
        db.flush()
        bandi.append(b)
    codice = uuid.uuid4().hex[:8]
    url = f"https://www.parco.example/albOnline/2026/PNGSLdocumento{codice}.pdf"
    # lo stesso PDF collegato a due bandi, vietato dal robots.txt
    for b in bandi:
        db.add(
            DocumentoBando(
                bando_id=b.id, url=url, titolo="Bando", errore=s.VIETATO_ROBOTS, tentativi=1
            )
        )
    db.commit()
    assert s.nome_dal_link(url) == f"pngsldocumento{codice}.pdf"
    assert (
        s.nome_scaricato(f"C:\Download\PNGSLdocumento{codice} (1).pdf")
        == f"pngsldocumento{codice}.pdf"
    )

    testo = "Possono partecipare le imprese del territorio del Parco. " * 10
    token = csrf(client, "/documenti/da-caricare")
    r = client.post(
        "/documenti/da-caricare",
        data={"csrf_token": token},
        files=[
            ("file", (f"PNGSLdocumento{codice} (1).pdf", _pdf(testo), "application/pdf")),
            ("file", ("altro.pdf", _pdf(testo), "application/pdf")),
        ],
    )
    assert r.status_code == 303
    pagina = client.get(r.headers["location"]).text
    assert "PDF caricati: 2" in pagina and "altro.pdf" in pagina
    db.expire_all()
    for d in db.query(DocumentoBando).filter(DocumentoBando.url == url):
        assert d.sha256 and d.errore is None and "imprese del territorio" in d.testo_estratto


def test_nomi_generici_non_abbinati(client, crea_utente, accedi, db):
    """«bando.pdf» di due enti diversi: non si sa di quale bando sia, niente abbinamento."""
    accedi(crea_utente())
    fonte = db.query(Fonte).filter(Fonte.codice == "prova_pdf").one_or_none() or Fonte(
        codice="prova_pdf", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    nome = f"bando{uuid.uuid4().hex[:6]}.pdf"
    for ente in ("comune-a", "comune-b"):
        b = Bando(
            fonte_id=fonte.id,
            id_esterno=uuid.uuid4().hex,
            titolo="Bando generico",
            stato="aperto",
            raw={},
            hash_contenuto="x" * 64,
        )
        db.add(b)
        db.flush()
        db.add(
            DocumentoBando(
                bando_id=b.id,
                url=f"https://{ente}.example/{nome}",
                titolo="Bando",
                errore=s.VIETATO_ROBOTS,
                tentativi=1,
            )
        )
    db.commit()
    assert nome.lower() not in s.documenti_per_nome(db)
    token = csrf(client, "/documenti/da-caricare")
    r = client.post(
        "/documenti/da-caricare",
        data={"csrf_token": token},
        files=[
            ("file", (nome, _pdf("Testo"), "application/pdf")),
            ("file", ("doppio.pdf", _pdf("A"), "application/pdf")),
            ("file", ("doppio (1).pdf", _pdf("B"), "application/pdf")),
        ],
    )
    pagina = client.get(r.headers["location"]).text
    assert "PDF caricati: 0" in pagina and "stesso nome" in pagina
