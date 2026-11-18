"""Bandi doppi, ordine per pertinenza, rinnovo della sessione, rotazione della chiave."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.bandi.doppioni import collega
from app.config import get_settings
from app.models import Bando, Fonte, Integrazione
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _fonte(db, codice, nome):
    f = db.scalar(select(Fonte).where(Fonte.codice == codice))
    if f is None:
        f = Fonte(codice=codice, nome=nome, url="https://esempio.test")
        db.add(f)
        db.flush()
    return f


def _nuovo(db, fonte, titolo, ente=None, chiusura=None):
    b = Bando(
        fonte_id=fonte.id,
        id_esterno=uuid.uuid4().hex,
        titolo=titolo,
        ente=ente,
        stato="aperto",
        data_chiusura=chiusura,
        raw={},
        hash_contenuto="x" * 64,
    )
    db.add(b)
    db.flush()
    return b


def test_bandi_doppi_collegati_e_nascosti(client, crea_utente, accedi, db):
    accedi(crea_utente())
    naz = _fonte(db, "incentivi_italia", "Incentivi.gov.it")
    lazio = _fonte(db, "regione_lazio", "Regione Lazio")
    sigla = uuid.uuid4().hex[:6]
    scad = date.today() + timedelta(days=30)
    regionale = _nuovo(db, lazio, f"Fondo Zeta{sigla} Lazio", chiusura=scad)
    doppio = _nuovo(db, naz, f"Fondo Zeta{sigla} Lazio", "Regione Lazio", scad + timedelta(2))
    # stesso titolo ma altra Regione: non è un doppione
    altro = _nuovo(db, naz, f"Fondo Zeta{sigla} Lazio", "Regione Siciliana", scad)
    db.commit()

    assert "collegati" in collega(db)
    db.expire_all()
    assert db.get(Bando, doppio.id).bando_canonico_id == regionale.id
    assert db.get(Bando, altro.id).bando_canonico_id is None
    assert db.get(Bando, regionale.id).bando_canonico_id is None

    pagina = client.get(f"/bandi?q=Zeta{sigla}").text
    assert f"/bandi/{regionale.id}" in pagina and f"/bandi/{doppio.id}" not in pagina
    assert f"/bandi/{doppio.id}" in client.get(f"/bandi/{regionale.id}").text
    assert "scheda principale" in client.get(f"/bandi/{doppio.id}").text

    # Idempotente; se il regionale chiude, il collegamento si toglie
    collega(db)
    regionale = db.get(Bando, regionale.id)
    regionale.stato = "chiuso"
    db.commit()
    collega(db)
    db.expire_all()
    assert db.get(Bando, doppio.id).bando_canonico_id is None


def test_ricerca_prima_i_titoli_pertinenti(client, crea_utente, accedi, db):
    accedi(crea_utente())
    fonte = _fonte(db, "prova_pertinenza", "Prova")
    sigla = uuid.uuid4().hex[:6]
    vicino = date.today() + timedelta(days=3)
    lontano = date.today() + timedelta(days=90)
    solo_descrizione = _nuovo(db, fonte, f"Contributi vari {sigla}", chiusura=vicino)
    solo_descrizione.descrizione = f"anche per l'agrivoltaico{sigla}"
    nel_titolo = _nuovo(db, fonte, f"Bando agrivoltaico{sigla} per le imprese", chiusura=lontano)
    db.commit()
    pagina = client.get(f"/bandi?q=agrivoltaico{sigla}").text
    # il titolo pertinente viene prima anche se scade dopo
    assert pagina.index(f"/bandi/{nel_titolo.id}") < pagina.index(f"/bandi/{solo_descrizione.id}")


def test_rinnovo_sessione_e_avviso(client, crea_utente, accedi):
    assert client.get("/sessione/rinnova", follow_redirects=False).status_code in (303, 401)
    accedi(crea_utente())
    assert client.get("/sessione/rinnova").status_code == 204
    home = client.get("/").text
    assert 'data-inattivita="' in home and "Resta collegato" in home and "js/app.js" in home


def test_rotazione_della_chiave_su_database(client, crea_utente, accedi, db, monkeypatch):
    from app.integrazioni.servizio import leggi_valori
    from app.sicurezza.rotazione import ruota

    accedi(crea_utente())
    token = csrf(client, "/impostazioni/integrazioni/openai")
    client.post(
        "/impostazioni/integrazioni/openai",
        data={"csrf_token": token, "api_key": "sk-prova-rotazione-1234"},
    )
    vecchia = get_settings().secret_key.get_secret_value()
    try:
        monkeypatch.setenv("SECRET_KEY", "k" * 48)
        monkeypatch.setenv("SECRET_KEY_PRECEDENTI", vecchia)
        get_settings.cache_clear()
        esito = ruota(db)
        assert esito["segreti_integrazioni"] >= 1 and esito["non_decifrabili"] == 0
        monkeypatch.delenv("SECRET_KEY_PRECEDENTI")
        get_settings.cache_clear()
        db.expire_all()
        assert leggi_valori(db, "openai")["api_key"] == "sk-prova-rotazione-1234"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
        db.commit()


def test_avviso_fonti_e_filtri_bandi(client, crea_utente, accedi, db, monkeypatch):
    from app.jobs import attivita
    from app.models import BandoRequisiti, Opportunita
    from tests.integration.test_funzioni_studio import _configura_smtp
    from tests.integration.test_pratiche import _cliente

    titolare = crea_utente()
    accedi(titolare)
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)),
    )
    fonte = _fonte(db, f"fonte_rotta_{uuid.uuid4().hex[:6]}", "Fonte rotta")
    fonte.fallimenti_consecutivi = 2
    db.commit()
    attivita.avviso_fonti()
    mie = [x for x in inviate if x[0] == titolare.email]
    assert mie and "Fonte rotta" in mie[0][2]
    fonte.fallimenti_consecutivi = 3  # una volta sola, quando si raggiunge la soglia
    db.commit()
    inviate.clear()
    attivita.avviso_fonti()
    assert not [x for x in inviate if "Fonte rotta" in x[2]]

    # Filtro «con campi da verificare» e clienti compatibili nella scheda del bando
    f = _fonte(db, "prova_pertinenza", "Prova")
    sigla = uuid.uuid4().hex[:6]
    incerto = _nuovo(db, f, f"Bando incerto {sigla}")
    certo = _nuovo(db, f, f"Bando certo {sigla}")
    db.add(
        BandoRequisiti(
            bando_id=incerto.id, territorio="nazionale", provenienza={}, da_verificare=["regime"]
        )
    )
    db.add(BandoRequisiti(bando_id=certo.id, territorio="nazionale", provenienza={}))
    a = _cliente(client, db)
    db.add(
        Opportunita(
            azienda_id=a.id, bando_id=certo.id, punteggio=77, motivi={}, stato="nuova",
            trovata_il=datetime.now(UTC),
        )
    )  # fmt: skip
    db.commit()
    pagina = client.get(f"/bandi?q={sigla}&verifica=1").text
    assert f"/bandi/{incerto.id}" in pagina and f"/bandi/{certo.id}" not in pagina
    scheda = client.get(f"/bandi/{certo.id}").text
    assert a.ragione_sociale in scheda and "Non pertinente" in scheda
