"""Sezione Automazioni: regole, bozze e invii automatici, sicurezze, permessi."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.automazioni import servizio as s
from app.models import AggiornamentoCliente, Automazione, Azienda, BandoRequisiti, Opportunita
from tests.integration.conftest import csrf
from tests.integration.test_funzioni_studio import _configura_smtp
from tests.integration.test_pratiche import _bando, _cliente

pytestmark = pytest.mark.integration
CREATI: list[int] = []


@pytest.fixture(autouse=True)
def _chiudi_bandi_creati(db):
    """I bandi di prova sono nazionali e aperti: chiusi alla fine, così l'abbinamento degli
    altri test non li propone ai loro clienti."""
    yield
    from app.models import Bando

    for bid in CREATI:
        b = db.get(Bando, bid)
        if b is not None:
            b.stato = "chiuso"
    db.commit()
    CREATI.clear()


def _cliente_con_consenso(client, db) -> Azienda:
    a = _cliente(client, db)
    ref = a.referenti[0]
    token = csrf(client, f"/clienti/{a.id}")
    client.post(
        f"/clienti/{a.id}/referenti/{ref.id}/consensi",
        data={"csrf_token": token, "consenso_email": "1"},
    )
    db.expire_all()
    return db.get(Azienda, a.id)


def _opportunita(db, a, punteggio=82, giorni=20):
    b = _bando(db)
    CREATI.append(b.id)
    b.data_chiusura = date.today() + timedelta(days=giorni)
    db.add(BandoRequisiti(bando_id=b.id, territorio="nazionale", provenienza={}))
    db.add(
        Opportunita(
            azienda_id=a.id,
            bando_id=b.id,
            punteggio=punteggio,
            motivi={"soddisfatti": ["Bando nazionale", "Dimensione ammessa"]},
            stato="nuova",
            trovata_il=datetime.now(UTC),
        )
    )
    db.commit()
    return b


def _crea(client, **campi) -> int:
    token = csrf(client, "/automazioni/nuova")
    evento = campi.get("evento", "nuovo_bando")
    base = (
        s.TESTO_RIEPILOGO
        if evento == "riepilogo"
        else next(p for p in s.PREDEFINITE if p["evento"] == evento)
    )
    dati = {
        "csrf_token": token,
        "nome": "Prova",
        "evento": evento,
        "destinatari": "tutti",
        "ambiti": list(s.AMBITI),
        "punteggio_minimo": "70",
        "azione": "bozza",
        "canale": "preferito",
        "oggetto": base["oggetto"],
        "testo": base["testo"],
        "attiva": "1",
        **campi,
    }
    r = client.post("/automazioni/nuova", data=dati)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])


def _messaggi(db, a, au_id):
    return list(
        db.scalars(
            select(AggiornamentoCliente).where(
                AggiornamentoCliente.azienda_id == a.id,
                AggiornamentoCliente.automazione_id == au_id,
            )
        )
    )


def test_nuovo_bando_bozza_e_sicurezze(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente_con_consenso(client, db)
    vecchio = _opportunita(db, a)  # trovato prima dell'accensione: niente arretrati
    au_id = _crea(client, nome="Nuovo bando adatto")
    au = db.get(Automazione, au_id)
    assert au.attiva and au.attiva_dal is not None
    s.esegui_una(db, au)
    db.commit()
    assert _messaggi(db, a, au_id) == []

    b1, b2 = _opportunita(db, a), _opportunita(db, a, punteggio=75)
    _opportunita(db, a, punteggio=60)  # sotto la soglia dell'automazione
    s.esegui_una(db, au)
    db.commit()
    (m,) = _messaggi(db, a, au_id)
    assert m.stato == "bozza" and set(m.bandi_citati) == {b1.id, b2.id}
    assert b1.titolo in m.testo and b2.titolo in m.testo and vecchio.titolo not in m.testo
    assert "{nome_referente}" in m.testo and a.ragione_sociale not in m.testo  # niente nomi
    assert m.oggetto.endswith("e un altro bando")
    # stesso giorno: niente secondo messaggio; giorni dopo: i bandi già citati non ripartono
    b3 = _opportunita(db, a)
    s.esegui_una(db, au)
    s.esegui_una(db, au, oggi=date.today() + timedelta(days=1))
    db.commit()
    messaggi = _messaggi(db, a, au_id)
    assert len(messaggi) == 2 and messaggi[-1].bandi_citati == [b3.id]

    # pagine: elenco, scheda con anteprima e registro, lista aggiornamenti con l'origine
    assert "Nuovo bando adatto" in client.get("/automazioni").text
    pagina = client.get(f"/automazioni/{au_id}?cliente={a.id}").text
    assert "Buongiorno Maria," in pagina and "Registro" in pagina
    assert "da «" in client.get("/aggiornamenti?stato=bozza").text


def test_promemoria_invio_automatico_e_filtri(client, crea_utente, accedi, db, monkeypatch):
    accedi(crea_utente())
    a = _cliente_con_consenso(client, db)
    vicino = _opportunita(db, a, giorni=10)
    _opportunita(db, a, giorni=40)  # scade più avanti: niente promemoria
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a_, oggetto, testo, rispondi_a=None: inviate.append((a_, oggetto, testo)),
    )
    au_id = _crea(client, nome="Promemoria", evento="scadenza", giorni_scadenza="15",
                  azione="invio")  # fmt: skip
    s.esegui_una(db, db.get(Automazione, au_id), base_url="http://test")
    db.commit()
    (m,) = _messaggi(db, a, au_id)
    assert m.stato == "inviato" and m.bandi_citati == [vicino.id]
    dest, oggetto, testo = inviate[-1]
    assert dest == "maria@esempio.it" and vicino.titolo in oggetto
    assert testo.startswith("Buongiorno Maria,") and "/disiscrizione/" in testo

    # solo enti, o senza bandi nazionali: per questa impresa niente
    for extra in ({"destinatari": "enti"}, {"ambiti": ["ue", "locali"]}):
        altro = _crea(client, nome="Filtro", evento="scadenza", **extra)
        s.esegui_una(db, db.get(Automazione, altro), oggi=date.today() + timedelta(days=2))
        db.commit()
        assert _messaggi(db, a, altro) == []
    # clienti scelti
    scelto = _crea(client, nome="Scelti", evento="scadenza", destinatari="scelti",
                   aziende_ids=[str(a.id)])  # fmt: skip
    s.esegui_una(db, db.get(Automazione, scelto), oggi=date.today() + timedelta(days=3))
    db.commit()
    assert len(_messaggi(db, a, scelto)) == 1
    # «Esegui adesso» dalla pagina: stesso giorno, il promemoria c'è già → nessun doppione
    token = csrf(client, f"/automazioni/{au_id}")
    r = client.post(f"/automazioni/{au_id}/esegui", data={"csrf_token": token})
    assert r.status_code == 303 and "Eseguita" in r.headers["location"]
    assert len(_messaggi(db, a, au_id)) == 1


def test_riepilogo_periodico(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente_con_consenso(client, db)
    b = _opportunita(db, a)
    au_id = _crea(client, nome="Riepilogo", evento="riepilogo", frequenza="settimanale",
                  destinatari="scelti", aziende_ids=[str(a.id)])  # fmt: skip
    au = db.get(Automazione, au_id)
    s.esegui_una(db, au)
    db.commit()
    (m,) = _messaggi(db, a, au_id)
    assert b.titolo in m.testo and "{riepilogo}" not in m.testo
    assert m.testo.startswith("Buongiorno {nome_referente},")
    # dopo 3 giorni non è ancora il momento, dopo 8 sì
    s.esegui_una(db, au, oggi=date.today() + timedelta(days=3))
    s.esegui_una(db, au, oggi=date.today() + timedelta(days=8))
    db.commit()
    assert len(_messaggi(db, a, au_id)) == 2


def test_modulo_errori_permessi_e_accensione(client, crea_utente, accedi, db):
    titolare = crea_utente()
    accedi(titolare)
    token = csrf(client, "/automazioni/nuova")
    r = client.post("/automazioni/nuova", data={
        "csrf_token": token, "nome": "Sbagliata", "evento": "nuovo_bando", "ambiti": ["ue"],
        "oggetto": "Ciao {nome}", "testo": "Niente bandi qui"})  # fmt: skip
    assert r.status_code == 400
    assert "Segnaposto sconosciuto" in r.text and "{elenco_bandi}" in r.text
    au_id = _crea(client, nome="Da spegnere", attiva="")
    assert not db.get(Automazione, au_id).attiva
    token = csrf(client, "/automazioni")
    client.post(f"/automazioni/{au_id}/accendi", data={"csrf_token": token, "attiva": "1"})
    db.expire_all()
    assert db.get(Automazione, au_id).attiva
    # modifica: un riepilogo senza {riepilogo} non si salva e il valore resta quello vecchio
    token = csrf(client, f"/automazioni/{au_id}")
    r = client.post(f"/automazioni/{au_id}", data={
        "csrf_token": token, "nome": "Cambiata", "evento": "riepilogo", "ambiti": ["ue"],
        "oggetto": "x", "testo": "senza corpo"})  # fmt: skip
    assert r.status_code == 400 and "{riepilogo}" in r.text
    db.expire_all()
    assert db.get(Automazione, au_id).nome == "Da spegnere"

    da_eliminare = _crea(client, nome="Da eliminare", attiva="")
    token = csrf(client, f"/automazioni/{da_eliminare}")
    r = client.post(f"/automazioni/{da_eliminare}/elimina", data={"csrf_token": token})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Automazione, da_eliminare) is None

    # il collaboratore vede ma non cambia
    client.cookies.clear()
    accedi(crea_utente("collaboratore"))
    assert client.get("/automazioni").status_code == 200
    assert "Nuova: bando adatto" not in client.get("/automazioni").text
    token = csrf(client, "/automazioni")
    assert client.post(f"/automazioni/{au_id}/accendi",
                       data={"csrf_token": token, "attiva": "0"}).status_code == 403  # fmt: skip
    assert client.get("/automazioni/nuova").status_code == 403


def test_automazioni_predefinite_spente(db):
    nomi = {au.nome: au for au in db.scalars(select(Automazione))}
    for p in s.PREDEFINITE:
        if p["nome"] in nomi:  # le crea la migrazione (spente)
            assert not s.errori_modello(p["oggetto"], p["testo"], p["evento"])
