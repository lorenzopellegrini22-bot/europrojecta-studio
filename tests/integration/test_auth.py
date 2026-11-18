from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.main import app
from app.models import LogAccesso, Sessione, Utente
from tests.integration.conftest import PASSWORD, codice_totp, csrf

pytestmark = pytest.mark.integration

# /disiscrizione: link nelle email ai clienti, protetto dal token firmato
ROUTE_PUBBLICHE = {"/login", "/health", "/disiscrizione/{token}"}


def _tutte_le_route(routes) -> list[APIRoute]:
    """Appiattisce le route, inclusi i router montati con include_router
    (FastAPI recente li avvolge in un oggetto con `original_router`)."""
    trovate: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            trovate.append(r)
        elif hasattr(r, "original_router"):
            trovate.extend(_tutte_le_route(r.original_router.routes))
    return trovate


def test_tutte_le_route_richiedono_il_login(client):
    """Ogni route, tranne login e /health, senza sessione rimanda al login (o dà 401/403)."""
    controllate = 0
    for route in _tutte_le_route(app.routes):
        if route.path in ROUTE_PUBBLICHE:
            continue
        url = route.path.replace("{codice}", "openai")
        for metodo in route.methods:
            r = client.request(metodo, url)
            assert r.status_code in (303, 401, 403), f"{metodo} {url} -> {r.status_code}"
            if r.status_code == 303:
                assert r.headers["location"] == "/login", f"{metodo} {url}"
            controllate += 1
    assert controllate >= 8


def test_flusso_completo_con_2fa(client, crea_utente, accedi, fabbrica):
    utente = crea_utente()
    codici = accedi(utente)
    assert len(codici) == 10

    r = client.get("/")
    assert r.status_code == 200
    assert "Buongiorno, Mario Rossi" in r.text

    # logout
    token = csrf(client, "/")
    r = client.post("/logout", data={"csrf_token": token})
    assert r.status_code == 303
    assert client.get("/").status_code == 303

    # secondo accesso: password poi codice TOTP del prossimo intervallo
    r = client.post("/login", data={"email": utente.email, "password": PASSWORD})
    assert r.headers["location"] == "/login/2fa"
    token = csrf(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"codice": codice_totp(fabbrica, utente.email, scarto=1), "csrf_token": token},
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_accesso_con_codice_di_recupero_monouso(client, crea_utente, accedi):
    utente = crea_utente()
    codici = accedi(utente)
    client.cookies.clear()

    def prova(codice: str) -> int:
        client.post("/login", data={"email": utente.email, "password": PASSWORD})
        token = csrf(client, "/login/2fa")
        return client.post("/login/2fa", data={"codice": codice, "csrf_token": token}).status_code

    assert prova(codici[0]) == 303
    client.cookies.clear()
    assert prova(codici[0]) == 400  # già usato


def test_senza_verifica_in_due_passaggi_basta_la_password(client, crea_utente, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verifica_due_passaggi", False)
    utente = crea_utente()
    r = client.post("/login", data={"email": utente.email, "password": "sbagliata"})
    assert r.status_code == 400
    r = client.post("/login", data={"email": utente.email, "password": PASSWORD})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/clienti").status_code == 200


def test_blocco_dopo_cinque_tentativi(client, crea_utente, db):
    utente = crea_utente()
    for _ in range(5):
        r = client.post("/login", data={"email": utente.email, "password": "sbagliata"})
        assert r.status_code == 400
    # anche la password giusta ora viene rifiutata
    r = client.post("/login", data={"email": utente.email, "password": PASSWORD})
    assert r.status_code == 400
    assert "bloccato" in r.text
    db.expire_all()
    u = db.get(Utente, utente.id)
    assert u.bloccato_fino_al is not None and u.bloccato_fino_al > datetime.now(UTC)
    esiti = db.scalars(select(LogAccesso.esito).where(LogAccesso.utente_id == utente.id)).all()
    assert esiti.count("password_errata") == 5 and "bloccato" in esiti


def test_email_inesistente_stesso_messaggio(client):
    r = client.post("/login", data={"email": "nessuno@studio.it", "password": "x" * 12})
    assert r.status_code == 400
    assert "Email o password non corretti" in r.text


def test_sessione_scaduta_per_inattivita(client, crea_utente, accedi, db):
    utente = crea_utente()
    accedi(utente)
    sessione = db.scalar(select(Sessione).where(Sessione.utente_id == utente.id))
    sessione.ultimo_uso = datetime.now(UTC) - timedelta(hours=3)
    db.commit()
    assert client.get("/").status_code == 303
    db.expire_all()
    assert db.scalar(select(Sessione).where(Sessione.utente_id == utente.id)) is None


def test_post_senza_csrf_rifiutato(client, crea_utente, accedi):
    accedi(crea_utente())
    r = client.post("/logout")
    assert r.status_code == 403
    assert client.get("/").status_code == 200  # sessione ancora valida


def test_collaboratore_non_vede_le_impostazioni(client, crea_utente, accedi):
    accedi(crea_utente(ruolo="collaboratore"))
    assert client.get("/").status_code == 200
    assert client.get("/impostazioni/integrazioni").status_code == 403


def test_htmx_senza_sessione_riceve_hx_redirect(client):
    r = client.post("/impostazioni/integrazioni/openai/verifica", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert r.headers["hx-redirect"] == "/login"
