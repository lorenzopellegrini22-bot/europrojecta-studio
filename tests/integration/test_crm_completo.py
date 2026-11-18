"""CRM completo: profilo esteso, tag, progetti, attività, scadenze, de minimis, consensi."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import AiutoDeMinimis, Attivita, Progetto, Scadenza
from tests.integration.conftest import csrf
from tests.integration.test_pratiche import _cliente

pytestmark = pytest.mark.integration


def test_scheda_cliente_completa(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(
        client,
        db,
        dipendenti="12",
        fatturato="1.800.000",
        totale_attivo="900.000",
        data_costituzione="2020-05-10",
        tag_testo="edilizia, Priorità alta, edilizia",
        frequenza_aggiornamenti="mensile",
    )
    db.refresh(a)
    assert sorted(t.nome for t in a.tag) == ["Priorità alta", "edilizia"]
    assert a.frequenza_aggiornamenti == "mensile"
    assert a.data_costituzione == date(2020, 5, 10)
    pagina = client.get(f"/clienti/{a.id}").text
    assert "Piccola" in pagina and "stima da dipendenti" in pagina  # 12 occupati
    # filtri della lista
    assert a.ragione_sociale in client.get("/clienti?tag=edilizia&dimensione=piccola").text
    assert a.ragione_sociale not in client.get("/clienti?dimensione=micro").text
    assert a.ragione_sociale in client.get("/clienti?regione=Toscana").text

    token = csrf(client, f"/clienti/{a.id}")
    # Progetto con categorie di spesa (quelle non previste si scartano)
    client.post(
        f"/clienti/{a.id}/progetti",
        data={
            "csrf_token": token,
            "titolo": "Nuova linea di produzione",
            "descrizione": "Acquisto di macchinari a controllo numerico",
            "importo_stimato": "250.000",
            "stato": "in_valutazione",
            "categorie": ["macchinari e attrezzature", "inventata"],
        },
    )
    p = db.scalar(select(Progetto).where(Progetto.azienda_id == a.id))
    assert p.categorie == ["macchinari e attrezzature"] and p.importo_stimato == 250000
    r = client.post(
        f"/clienti/{a.id}/progetti/{p.id}",
        data={"csrf_token": token, "titolo": "Linea 2", "stato": "in_corso"},
    )
    assert r.status_code == 303
    db.refresh(p)
    assert p.titolo == "Linea 2" and p.stato == "in_corso"
    assert "Linea 2" in client.get(f"/clienti/{a.id}/progetti/{p.id}").text

    # Attività e scadenze
    client.post(
        f"/clienti/{a.id}/attivita",
        data={"csrf_token": token, "tipo": "chiamata", "testo": "Sentito per i preventivi"},
    )
    assert db.scalar(select(Attivita).where(Attivita.azienda_id == a.id)).tipo == "chiamata"
    domani = date.today() + timedelta(days=1)
    client.post(
        f"/clienti/{a.id}/scadenze",
        data={"csrf_token": token, "titolo": "Richiamare il cliente", "data": domani.isoformat()},
    )
    sc = db.scalar(select(Scadenza).where(Scadenza.azienda_id == a.id))
    assert "Richiamare il cliente" in client.get("/scadenzario").text
    client.post(
        f"/clienti/{a.id}/scadenze/{sc.id}", data={"csrf_token": token, "azione": "completa"}
    )
    db.refresh(sc)
    assert sc.completata
    assert "Richiamare il cliente" not in client.get("/scadenzario").text

    # De minimis: plafond residuo
    client.post(
        f"/clienti/{a.id}/de-minimis",
        data={
            "csrf_token": token,
            "data_concessione": (date.today() - timedelta(days=100)).isoformat(),
            "importo": "100.000",
            "regolamento": "generale",
            "ente": "Regione",
        },
    )
    assert db.scalar(select(AiutoDeMinimis).where(AiutoDeMinimis.azienda_id == a.id))
    assert "200.000" in client.get(f"/clienti/{a.id}").text  # 300.000 - 100.000

    # Consensi del referente
    ref = a.referenti[0]
    client.post(
        f"/clienti/{a.id}/referenti/{ref.id}/consensi",
        data={"csrf_token": token, "consenso_email": "1", "canale_preferito": "email"},
    )
    db.refresh(ref)
    assert ref.consenso_email and not ref.consenso_whatsapp and ref.consenso_il is not None
