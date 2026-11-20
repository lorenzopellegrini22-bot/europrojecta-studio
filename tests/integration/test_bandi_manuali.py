"""Bandi inseriti a mano: le misure che le fonti automatiche non hanno."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.bandi import manuali
from app.models import Azienda, Bando, ElementoCestino, Fonte, Opportunita, Sede
from app.opportunita.abbinamento import abbina
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _modulo(**valori):
    base = {
        "titolo": f"Resto al Sud 2.0 prova {uuid.uuid4().hex[:6]}",
        "ente": "Invitalia",
        "descrizione": "Contributi a fondo perduto per avviare attività di lavoro autonomo.",
        "link_ufficiale": "https://www.invitalia.it/incentivi-e-strumenti/resto-al-sud-20",
        "data_apertura": (date.today() - timedelta(days=30)).isoformat(),
        "data_chiusura": "",
        "ambito": "regionale",
        "regioni": ["Campania", "Calabria"],
        "dimensioni": ["micro", "piccola"],
        "forme": ["Contributo/Fondo perduto"],
        "importo_max": "120.000",
    }
    base.update(valori)
    return base


def _crea(client, token, **valori):
    r = client.post("/bandi/nuovo", data={"csrf_token": token, **_modulo(**valori)})
    assert r.status_code == 303, r.text[:500]
    return int(r.headers["location"].split("/")[2].split("?")[0])


def test_bando_inserito_a_mano_come_gli_altri(client, crea_utente, accedi, db):
    accedi(crea_utente())
    token = csrf(client, "/bandi/nuovo")
    assert "Aggiungi un bando" in client.get("/bandi").text
    bid = _crea(client, token)
    b = db.get(Bando, bid)
    assert b.stato == "aperto" and b.fonte.codice == manuali.CODICE_FONTE
    assert b.requisiti.territorio == "regionale"
    assert set(b.requisiti.regioni) == {"Campania", "Calabria"}
    assert b.requisiti.dimensioni == ["micro", "piccola"]
    assert b.requisiti.importo_max == 120000
    scheda = client.get(f"/bandi/{bid}?inserito=1").text
    assert "inserito dallo studio" in scheda and f"/bandi/{bid}/modifica" in scheda
    # ricerca e filtro per regione
    assert (
        b.titolo in client.get("/bandi", params={"q": "resto al sud", "regione": "Campania"}).text
    )
    assert b.titolo not in client.get("/bandi", params={"regione": "Veneto"}).text
    # la pagina Fonti non mostra la fonte «manuale»
    assert manuali.NOME_FONTE not in client.get("/fonti").text

    # opportunità per un cliente campano
    a = Azienda(ragione_sociale=f"Manuale Prova {uuid.uuid4().hex[:6]}", stato_cliente="attivo",
                dimensione_manuale="micro")  # fmt: skip
    a.sedi.append(Sede(tipo="legale", comune_testo="Napoli", provincia="NA", regione="Campania"))
    db.add(a)
    db.commit()
    try:
        abbina(db)
        assert db.scalar(
            select(Opportunita).where(Opportunita.azienda_id == a.id, Opportunita.bando_id == bid)
        )
    finally:
        db.execute(delete(Azienda).where(Azienda.id == a.id))
        db.commit()


def test_modifica_errori_e_cestino(client, crea_utente, accedi, db):
    accedi(crea_utente())
    token = csrf(client, "/bandi/nuovo")
    r = client.post("/bandi/nuovo", data={"csrf_token": token, **_modulo(titolo="", regioni=[])})
    assert r.status_code == 400
    assert "Il titolo è obbligatorio" in r.text and "almeno una regione" in r.text
    senza_https = _modulo(link_ufficiale="www.invitalia.it")
    r = client.post("/bandi/nuovo", data={"csrf_token": token, **senza_https})
    assert r.status_code == 400 and "https://" in r.text

    bid = _crea(client, token)
    pagina = client.get(f"/bandi/{bid}/modifica").text
    assert "Campania" in pagina and "120000" in pagina
    chiusura = (date.today() - timedelta(days=1)).isoformat()
    nuovi = _modulo(titolo="Resto al Sud 2.0 aggiornato", ambito="nazionale",
                    data_chiusura=chiusura)  # fmt: skip
    r = client.post(f"/bandi/{bid}/modifica", data={"csrf_token": token, **nuovi})
    assert r.status_code == 303
    db.expire_all()
    b = db.get(Bando, bid)
    assert b.titolo == "Resto al Sud 2.0 aggiornato" and b.stato == "chiuso"
    assert b.requisiti.territorio == "nazionale"

    r = client.post(f"/bandi/{bid}/elimina", data={"csrf_token": token})
    assert r.headers["location"].startswith("/bandi?cestino=")
    db.expire_all()
    assert db.get(Bando, bid) is None
    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "bando",
                                                ElementoCestino.oggetto_id == bid))  # fmt: skip
    client.post(f"/cestino/{e.id}/ripristina", data={"csrf_token": token})
    db.expire_all()
    assert db.get(Bando, bid).titolo == "Resto al Sud 2.0 aggiornato"


def test_solo_i_bandi_manuali_si_modificano(client, crea_utente, accedi, db):
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_manuali")) or Fonte(
        codice="prova_manuali", nome="Prova", url="https://esempio.test"
    )
    db.add(fonte)
    db.flush()
    automatico = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Dalla fonte",
                       stato="aperto", raw={}, hash_contenuto="m" * 64)  # fmt: skip
    db.add(automatico)
    db.commit()
    accedi(crea_utente())
    token = csrf(client, "/bandi/nuovo")
    assert client.get(f"/bandi/{automatico.id}/modifica").status_code == 404
    r = client.post(f"/bandi/{automatico.id}/elimina", data={"csrf_token": token})
    assert r.status_code == 404
    assert f"/bandi/{automatico.id}/modifica" not in client.get(f"/bandi/{automatico.id}").text
    # chi è in sola lettura non vede il pulsante e non può inserire
    client.cookies.clear()
    accedi(crea_utente("lettura"))
    assert "Aggiungi un bando" not in client.get("/bandi").text
    token = csrf(client, "/bandi")
    r = client.post("/bandi/nuovo", data={"csrf_token": token, **_modulo()})
    assert r.status_code == 403


def test_stato_aggiornato_ogni_notte(client, crea_utente, accedi, db):
    accedi(crea_utente())
    token = csrf(client, "/bandi/nuovo")
    domani = (date.today() + timedelta(days=1)).isoformat()
    bid = _crea(client, token, data_chiusura=domani)
    assert db.get(Bando, bid).stato == "aperto"
    assert manuali.aggiorna_stati(db, date.today() + timedelta(days=2)) >= 1
    db.commit()
    assert db.get(Bando, bid).stato == "chiuso"
