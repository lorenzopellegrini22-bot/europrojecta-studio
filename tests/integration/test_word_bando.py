"""Scarica in Word i PDF dei bandi: link nella Documentazione e file .docx."""

import io
import uuid

import pymupdf
import pytest
from docx import Document
from sqlalchemy import select

from app.documenti import servizio
from app.models import Bando, DocumentoBando, Fonte
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _bando(db) -> Bando:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_word")) or Fonte(
        codice="prova_word", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando Word",
              stato="aperto", raw={}, hash_contenuto="w" * 64)  # fmt: skip
    db.add(b)
    db.flush()
    return b


def _pdf(testo: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), testo, fontsize=11)
    return doc.tobytes()


def test_scarica_in_word(client, crea_utente, accedi, db):
    b = _bando(db)
    con_file = DocumentoBando(bando_id=b.id, url=f"https://ente.example/{uuid.uuid4().hex}.pdf",
                              titolo="Avviso pubblico ( pdf 120 KB)", tentativi=0,
                              percorso=servizio._salva(_pdf("Contributo per le imprese.")),
                              testo_estratto="Contributo per le imprese.", pagine=1)  # fmt: skip
    solo_testo = DocumentoBando(bando_id=b.id, url=f"https://ente.example/{uuid.uuid4().hex}.pdf",
                                titolo="FAQ", tentativi=0, percorso="../../fuori.pdf",
                                testo_estratto="Domande frequenti.", pagine=1)  # fmt: skip
    vuoto = DocumentoBando(bando_id=b.id, url=f"https://ente.example/{uuid.uuid4().hex}.pdf",
                           titolo="Da scaricare", tentativi=0)  # fmt: skip
    db.add_all([con_file, solo_testo, vuoto])
    db.commit()
    accedi(crea_utente())

    scheda = client.get(f"/bandi/{b.id}").text
    assert f"/bandi/{b.id}/documenti/{con_file.id}/word" in scheda
    assert f"/bandi/{b.id}/documenti/{solo_testo.id}/word" in scheda
    assert f"/bandi/{b.id}/documenti/{vuoto.id}/word" not in scheda

    r = client.get(f"/bandi/{b.id}/documenti/{con_file.id}/word")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "Avviso%20pubblico.docx" in r.headers["content-disposition"]
    assert "Contributo per le imprese." in [
        p.text for p in Document(io.BytesIO(r.content)).paragraphs
    ]

    # il Word si tiene accanto al PDF: la seconda volta non si riconverte
    from app.documenti import word

    pdf = servizio.cartella() / con_file.percorso
    copia = pdf.with_name(f"{pdf.stem}.{word.VERSIONE}.docx")
    assert copia.is_file()
    copia.write_bytes(r.content + b"")  # stessa copia
    assert client.get(f"/bandi/{b.id}/documenti/{con_file.id}/word").content == copia.read_bytes()

    # percorso fuori dalla cartella: non si legge, si usa il testo già letto
    r = client.get(f"/bandi/{b.id}/documenti/{solo_testo.id}/word")
    assert r.status_code == 200
    assert Document(io.BytesIO(r.content)).paragraphs[0].text == "Domande frequenti."

    assert client.get(f"/bandi/{b.id}/documenti/{vuoto.id}/word").status_code == 404
    altro = _bando(db)
    db.commit()
    assert client.get(f"/bandi/{altro.id}/documenti/{con_file.id}/word").status_code == 404


def test_word_richiede_accesso(client, db):
    r = client.get("/bandi/1/documenti/1/word", follow_redirects=False)
    assert r.status_code in (303, 401)


def test_elimina_pdf_caricato_a_mano(client, crea_utente, accedi, db):
    b = _bando(db)
    accedi(crea_utente())
    token = csrf(client, f"/bandi/{b.id}")
    r = client.post(
        f"/bandi/{b.id}/documenti",
        data={"csrf_token": token},
        files={"file": ("sbagliato.pdf", _pdf("Testo caricato per errore."), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "esito=ok" in r.headers["location"]
    d = db.scalar(select(DocumentoBando).where(DocumentoBando.bando_id == b.id))
    file, did = servizio.cartella() / d.percorso, d.id
    assert file.is_file()
    client.get(f"/bandi/{b.id}/documenti/{d.id}/word")  # crea la copia Word accanto al PDF
    scheda = client.get(f"/bandi/{b.id}").text
    assert f"/bandi/{b.id}/documenti/{d.id}/elimina" in scheda

    r = client.post(f"/bandi/{b.id}/documenti/{d.id}/elimina", data={"csrf_token": token},
                    follow_redirects=False)  # fmt: skip
    assert r.status_code == 303 and "cestino=" in r.headers["location"]
    db.expire_all()
    assert db.get(DocumentoBando, did) is None
    assert file.exists()  # resta finché il PDF è nel cestino
    from app.cestino import servizio as cestino
    from app.models import ElementoCestino

    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "documento_bando",
                                                ElementoCestino.oggetto_id == did))  # fmt: skip
    cestino.elimina_definitivamente(db, e)
    db.commit()
    assert not file.exists() and not list(file.parent.glob(f"{file.stem}.*.docx"))


def test_non_si_eliminano_i_pdf_della_fonte(client, crea_utente, accedi, db):
    b = _bando(db)
    d = DocumentoBando(bando_id=b.id, url="https://ente.example/bando.pdf", titolo="Bando",
                       tentativi=0, testo_estratto="Testo.", pagine=1)  # fmt: skip
    db.add(d)
    db.commit()
    accedi(crea_utente())
    token = csrf(client, f"/bandi/{b.id}")
    assert f"/documenti/{d.id}/elimina" not in client.get(f"/bandi/{b.id}").text
    r = client.post(f"/bandi/{b.id}/documenti/{d.id}/elimina", data={"csrf_token": token})
    assert r.status_code == 404
