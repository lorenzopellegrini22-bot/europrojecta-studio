"""Report PDF su un bando: bozza precompilata, approvazione, PDF (dove c'è WeasyPrint)."""

import io

import pytest
from sqlalchemy import select

from app.models import BandoRequisiti, ReportBando
from tests.integration.conftest import csrf
from tests.integration.test_pratiche import _bando, _cliente

pytestmark = pytest.mark.integration


def _weasyprint_disponibile() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def test_report_bozza_approvazione_pdf(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(client, db)
    b = _bando(db)
    db.add(
        BandoRequisiti(
            bando_id=b.id,
            territorio="nazionale",
            provenienza={},
            sintesi="Contributo per la digitalizzazione delle PMI.",
            forma_agevolazione=["Contributo/Fondo perduto"],
            percentuale=50,
            da_verificare=["regime"],
        )
    )
    db.commit()
    token = csrf(client, f"/clienti/{a.id}")
    r = client.post(f"/clienti/{a.id}/report/{b.id}", data={"csrf_token": token})
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    rep = db.get(ReportBando, rid)
    assert rep.contenuto["sintesi"].startswith("Contributo per la digitalizzazione")
    assert "Campo da verificare sul bando: regime" in rep.contenuto["cosa_serve"]
    # Prima dell'approvazione niente PDF
    r = client.get(f"/report/{rid}/pdf")
    assert r.status_code == 303
    assert "Approva il report prima" in client.get(r.headers["location"]).text
    token = csrf(client, f"/report/{rid}")
    client.post(
        f"/report/{rid}",
        data={
            "csrf_token": token,
            "azione": "approva",
            "sintesi": "Sintesi corretta dal consulente.",
            "perche_adatto": "Siete una PMI.",
            "cosa_serve": "Visura",
            "scadenze": "",
            "note": "",
        },
    )
    db.expire_all()
    rep = db.get(ReportBando, rid)
    assert (
        rep.stato == "approvato" and rep.contenuto["sintesi"] == "Sintesi corretta dal consulente."
    )
    # Lo stesso bando per lo stesso cliente riapre il report esistente
    r = client.post(f"/clienti/{a.id}/report/{b.id}", data={"csrf_token": token})
    assert r.headers["location"] == f"/report/{rid}"
    assert len(list(db.scalars(select(ReportBando).where(ReportBando.azienda_id == a.id)))) == 1

    r = client.get(f"/report/{rid}/pdf")
    if _weasyprint_disponibile():
        from pypdf import PdfReader

        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        testo = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        assert "Sintesi corretta dal consulente" in testo and a.ragione_sociale in testo
        assert "CC BY 4.0" in testo
    else:
        assert r.status_code == 303 and "WeasyPrint" in client.get(r.headers["location"]).text
