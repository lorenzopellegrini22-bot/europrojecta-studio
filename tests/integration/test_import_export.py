"""Import dei clienti da CSV ed export completo."""

import io
import zipfile

import pytest
from sqlalchemy import select

from app.models import Azienda, ImportJob
from tests.integration.conftest import csrf
from tests.integration.test_pratiche import _piva

pytestmark = pytest.mark.integration


def test_import_prova_vuoto_import_e_reimport(client, crea_utente, accedi, db):
    accedi(crea_utente())
    p1, p2 = _piva(db), None
    while p2 is None or p2 == p1:
        p2 = _piva(db)
    csv_testo = (
        "Denominazione;Partita IVA;Comune;Provincia;Regione;Dipendenti;"
        "Nome;Cognome;Email referente\n"
        f"Alfa Costruzioni Srl;{p1};Viterbo;VT;Lazio;12;Anna;Bianchi;anna@alfa.it\n"
        f"Alfa Costruzioni Srl;{p1};Viterbo;VT;Lazio;12;Luca;Verdi;luca@alfa.it\n"
        f"Beta Snc;{p2};Terni;TR;umbria;;Carlo;Neri;\n"
        "Senza partita iva;;Roma;RM;Lazio;3;;;\n"
    ).encode("cp1252")
    token = csrf(client, "/import")
    r = client.post(
        "/import",
        data={"csrf_token": token, "tipo": "aziende"},
        files={"file": ("export.csv", csv_testo, "text/csv")},
    )
    assert r.status_code == 303
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    pagina = client.get(f"/import/{job_id}").text
    assert "manca la partita IVA" in pagina and "Alfa Costruzioni" in pagina

    # Prova a vuoto: niente nel database
    r = client.post(f"/import/{job_id}/esegui", data={"csrf_token": token, "modo": "prova"})
    assert "Prova+a+vuoto" in r.headers["location"] or "Prova%20a%20vuoto" in r.headers["location"]
    assert db.scalar(select(Azienda).where(Azienda.partita_iva == p1)) is None
    job = db.get(ImportJob, job_id)
    assert job.stato == "provato" and job.righe_scartate == 1

    # Import vero
    client.post(f"/import/{job_id}/esegui", data={"csrf_token": token, "modo": "importa"})
    db.expire_all()
    alfa = db.scalar(select(Azienda).where(Azienda.partita_iva == p1))
    assert alfa.ragione_sociale == "Alfa Costruzioni Srl" and alfa.dipendenti == 12
    assert sorted(r.nome for r in alfa.referenti) == ["Anna", "Luca"]
    assert alfa.sede_legale.provincia == "VT"
    beta = db.scalar(select(Azienda).where(Azienda.partita_iva == p2))
    assert beta.sede_legale.regione == "Umbria"
    assert client.get(f"/import/{job_id}/errori.csv").text.count("\n") == 2

    # Reimport dello stesso file: nessun doppione
    client.post(f"/import/{job_id}/esegui", data={"csrf_token": token, "modo": "importa"})
    db.expire_all()
    assert len(list(db.scalars(select(Azienda).where(Azienda.partita_iva == p1)))) == 1
    alfa = db.scalar(select(Azienda).where(Azienda.partita_iva == p1))
    assert len(alfa.referenti) == 2


def test_export_completo_solo_titolare(client, crea_utente, accedi, db):
    accedi(crea_utente("collaboratore"))
    assert client.get("/export/completo.zip").status_code == 403
    assert client.get("/import").status_code == 403
    client.cookies.clear()
    accedi(crea_utente())
    r = client.get("/export/completo.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    nomi = z.namelist()
    assert "dati/aziende.csv" in nomi and "dati/progetti.csv" in nomi and "LEGGIMI.txt" in nomi
    assert "dati/note.csv" in nomi and "dati/bandi_seguiti.csv" in nomi
    intestazione = z.read("dati/aziende.csv").decode("utf-8-sig").splitlines()[0]
    assert "ragione_sociale" in intestazione and "partita_iva" in intestazione
