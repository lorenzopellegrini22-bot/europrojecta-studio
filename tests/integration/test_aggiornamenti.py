"""Aggiornamenti ai clienti: bozze, approvazione, invio email/WhatsApp, disiscrizione."""

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.aggiornamenti import servizio as s
from app.aggiornamenti.canali import CanaleWhatsApp, numero_whatsapp
from app.models import AggiornamentoCliente, BandoRequisiti, Opportunita
from tests.integration.conftest import csrf
from tests.integration.test_funzioni_studio import _configura_smtp
from tests.integration.test_pratiche import _bando, _cliente

pytestmark = pytest.mark.integration


def _scenario(client, db):
    a = _cliente(client, db, frequenza_aggiornamenti="settimanale")
    ref = a.referenti[0]
    b = _bando(db)
    db.add(BandoRequisiti(bando_id=b.id, territorio="nazionale", provenienza={}))
    db.add(
        Opportunita(
            azienda_id=a.id,
            bando_id=b.id,
            punteggio=82,
            motivi={"soddisfatti": ["Bando nazionale"], "da_verificare": []},
            stato="interessante",
            trovata_il=s.datetime.now(s.UTC),
        )
    )
    db.commit()
    return a, ref, b


def test_bozza_approvazione_invio_e_disiscrizione(client, crea_utente, accedi, db, monkeypatch):
    utente = crea_utente()
    accedi(utente)
    a, ref, b = _scenario(client, db)

    # Senza consenso non nasce nessuna bozza
    assert s.crea_bozza(db, a) is None
    token = csrf(client, f"/clienti/{a.id}")
    client.post(
        f"/clienti/{a.id}/referenti/{ref.id}/consensi",
        data={"csrf_token": token, "consenso_email": "1"},
    )
    r = client.post(f"/clienti/{a.id}/aggiornamenti", data={"csrf_token": token})
    ag_id = int(r.headers["location"].rsplit("/", 1)[1])
    ag = db.get(AggiornamentoCliente, ag_id)
    assert ag.stato == "bozza" and ag.canale == "email" and ag.bandi_citati == [b.id]
    # Il testo salvato ha i segnaposto, non i nomi
    assert "{nome_referente}" in ag.testo and a.ragione_sociale not in ag.testo
    assert b.titolo in ag.testo
    pagina = client.get(f"/aggiornamenti/{ag_id}").text
    assert "Maria" in pagina  # anteprima con i segnaposto sostituiti

    # Approva e invia (posta simulata)
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a_, oggetto, testo, rispondi_a=None: inviate.append((a_, oggetto, testo)),
    )
    token = csrf(client, f"/aggiornamenti/{ag_id}")
    r = client.post(
        f"/aggiornamenti/{ag_id}",
        data={
            "csrf_token": token,
            "azione": "invia",
            "oggetto": ag.oggetto,
            "testo": ag.testo,
            "referente_canale": f"{ref.id}:email",
        },
    )
    assert "esito=ok" in r.headers["location"]
    dest, oggetto, testo = inviate[-1]
    assert dest == "maria@esempio.it" and a.ragione_sociale in oggetto
    assert testo.startswith("Buongiorno Maria,") and "/disiscrizione/" in testo
    db.expire_all()
    assert db.get(AggiornamentoCliente, ag_id).stato == "inviato"

    # La prossima bozza non ripete lo stesso bando
    successiva = s.crea_bozza(db, db.get(type(a), a.id))
    assert not successiva.bandi_citati
    db.commit()

    # Disiscrizione dal link (pagina pubblica, anche senza login)
    link = testo.split("/disiscrizione/")[1].strip()
    client.cookies.clear()
    assert "Confermi" in client.get(f"/disiscrizione/{link}").text
    assert "Fatto" in client.post(f"/disiscrizione/{link}").text
    assert client.get("/disiscrizione/1.falso").status_code == 404
    db.expire_all()
    ref = db.get(type(ref), ref.id)
    assert ref.disiscritto_il is not None and not ref.consenso_email
    # Dopo la disiscrizione l'invio di una bozza già pronta viene bloccato
    successiva = db.get(AggiornamentoCliente, successiva.id)
    successiva.stato = "approvato"
    esito = s.invia(db, successiva, utente, "http://test")
    assert not esito.ok and "consenso" in esito.errore


def test_canale_whatsapp_con_api_finta(db):
    chiamate = []

    def risposta(request: httpx.Request) -> httpx.Response:
        chiamate.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.PROVA"}]})

    from app.integrazioni.servizio import salva
    from app.models import Integrazione, Utente

    db.query(Integrazione).filter(Integrazione.codice == "whatsapp").delete()
    db.commit()
    utente = db.scalar(select(Utente).limit(1))
    if utente is None:
        pytest.skip("serve un utente")
    salva(
        db,
        "whatsapp",
        {
            "phone_number_id": "123456",
            "business_account_id": "999",
            "access_token": "tok-finto",
            "nome_modello": "aggiornamento_cliente",
        },
        set(),
        utente,
    )
    canale = CanaleWhatsApp(db, httpx.Client(transport=httpx.MockTransport(risposta)))
    assert canale.disponibile()
    esito = canale.invia("333 1234567", "", "Testo\n\ndell'aggiornamento", "Maria")
    assert esito.ok and esito.id_messaggio == "wamid.PROVA"
    req = chiamate[0]
    assert str(req.url) == "https://graph.facebook.com/v27.0/123456/messages"
    assert req.headers["authorization"] == "Bearer tok-finto"
    corpo = json.loads(req.content)
    assert corpo["to"] == "393331234567" and corpo["type"] == "template"
    assert corpo["template"]["name"] == "aggiornamento_cliente"
    assert corpo["template"]["language"] == {"code": "it"}
    parametri = corpo["template"]["components"][0]["parameters"]
    assert parametri[0]["text"] == "Maria" and parametri[1]["text"] == "Testo dell'aggiornamento"
    db.query(Integrazione).filter(Integrazione.codice == "whatsapp").delete()
    db.commit()


def test_numero_whatsapp():
    assert numero_whatsapp("+39 333 123 4567") == "393331234567"
    assert numero_whatsapp("0039 3331234567") == "393331234567"
    assert numero_whatsapp("3331234567") == "393331234567"
    assert numero_whatsapp("abc") is None


def test_bozze_periodiche(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a, ref, _b = _scenario(client, db)
    ref.consenso_email = True
    db.commit()
    s.bozze_periodiche(db)
    prime = list(
        db.scalars(select(AggiornamentoCliente).where(AggiornamentoCliente.azienda_id == a.id))
    )
    assert len(prime) == 1
    s.bozze_periodiche(db)  # stessa settimana: nessuna nuova bozza
    assert (
        len(
            list(
                db.scalars(
                    select(AggiornamentoCliente).where(AggiornamentoCliente.azienda_id == a.id)
                )
            )
        )
        == 1
    )
    s.bozze_periodiche(db, oggi=date.today() + timedelta(days=8))
    assert (
        len(
            list(
                db.scalars(
                    select(AggiornamentoCliente).where(AggiornamentoCliente.azienda_id == a.id)
                )
            )
        )
        == 2
    )
