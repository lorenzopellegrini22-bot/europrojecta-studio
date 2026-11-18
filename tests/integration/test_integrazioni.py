import pytest
from sqlalchemy import select

from app.integrazioni import router as integrazioni_router
from app.integrazioni.servizio import leggi_valori
from app.integrazioni.verifiche import EsitoVerifica
from app.models import AuditLog, Integrazione
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration

CHIAVE = "sk-proj-chiave-finta-di-prova-9876"
URL = "/impostazioni/integrazioni/openai"


@pytest.fixture
def titolare(client, crea_utente, accedi):
    u = crea_utente()
    accedi(u)
    return u


@pytest.fixture(autouse=True)
def pulisci_integrazioni(db):
    db.query(Integrazione).delete()
    db.commit()


def test_elenco_integrazioni(client, titolare):
    r = client.get("/impostazioni/integrazioni")
    assert r.status_code == 200
    for nome in ("OpenAI", "Email (SMTP)", "WhatsApp Business"):
        assert nome in r.text
    assert "Non configurata" in r.text


def test_chiave_salvata_cifrata_e_mai_mostrata(client, titolare, db):
    r = client.post(URL, data={"api_key": CHIAVE, "csrf_token": csrf(client, URL)})
    assert r.status_code == 303

    riga = db.get(Integrazione, "openai")
    assert CHIAVE not in str(riga.segreti)  # nel db solo il valore cifrato
    assert riga.anteprime["api_key"] == "…9876"
    assert leggi_valori(db, "openai")["api_key"] == CHIAVE

    pagina = client.get(URL).text
    assert CHIAVE not in pagina
    assert "…9876" in pagina

    # l'audit registra la modifica ma non il valore
    voce = db.scalars(
        select(AuditLog).where(AuditLog.entita == "integrazione").order_by(AuditLog.id.desc())
    ).first()
    assert voce.differenze == {"api_key": "modificato"}
    assert CHIAVE not in str(voce.differenze)


def test_campo_segreto_vuoto_mantiene_la_chiave(client, titolare, db):
    client.post(URL, data={"api_key": CHIAVE, "csrf_token": csrf(client, URL)})
    client.post(URL, data={"api_key": "", "csrf_token": csrf(client, URL)})
    db.expire_all()
    assert leggi_valori(db, "openai")["api_key"] == CHIAVE


def test_rimozione_chiave(client, titolare, db):
    client.post(URL, data={"api_key": CHIAVE, "csrf_token": csrf(client, URL)})
    client.post(URL, data={"rimuovi_api_key": "1", "csrf_token": csrf(client, URL)})
    db.expire_all()
    assert leggi_valori(db, "openai")["api_key"] is None


def test_variabile_ambiente_come_alternativa(client, titolare, db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-da-ambiente-0000")
    assert leggi_valori(db, "openai")["api_key"] == "sk-da-ambiente-0000"
    pagina = client.get(URL).text
    assert "OPENAI_API_KEY" in pagina, pagina[pagina.find("API key") : pagina.find("API key") + 900]
    # il valore salvato dall'app prevale
    client.post(URL, data={"api_key": CHIAVE, "csrf_token": csrf(client, URL)})
    db.expire_all()
    assert leggi_valori(db, "openai")["api_key"] == CHIAVE


def test_smtp_validazione_porta(client, titolare):
    url = "/impostazioni/integrazioni/smtp"
    r = client.post(
        url,
        data={
            "host": "smtp.esempio.it",
            "porta": "abc",
            "sicurezza": "starttls",
            "utente": "u",
            "mittente": "studio@esempio.it",
            "csrf_token": csrf(client, url),
        },
    )
    assert r.status_code == 400
    assert "Deve essere un numero intero" in r.text


def test_verifica_connessione_registra_esito(client, titolare, db, monkeypatch):
    chiamate = []

    def finta_verifica(valori):
        chiamate.append(valori["api_key"])
        return EsitoVerifica(True, "Connessione riuscita.")

    monkeypatch.setitem(integrazioni_router.VERIFICHE, "openai", finta_verifica)
    client.post(URL, data={"api_key": CHIAVE, "csrf_token": csrf(client, URL)})
    token = csrf(client, URL)
    r = client.post(f"{URL}/verifica", headers={"HX-Request": "true", "X-CSRF-Token": token})
    assert r.status_code == 200
    assert "Connessione riuscita." in r.text
    assert chiamate == [CHIAVE]
    db.expire_all()
    assert db.get(Integrazione, "openai").verifica_ok is True


def test_integrazione_inesistente(client, titolare):
    assert client.get("/impostazioni/integrazioni/inesistente").status_code == 404
