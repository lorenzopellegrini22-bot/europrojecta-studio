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
