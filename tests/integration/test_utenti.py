# ruff: noqa: E501
"""Impostazioni → Utenti e permessi: creazione, sezioni, sola lettura, disattivazione."""

import re
import uuid

import pytest
from sqlalchemy import select

from app.auth.permessi import puo_vedere, sezione_di
from app.models import Utente
from tests.integration.conftest import codice_totp, csrf

pytestmark = pytest.mark.integration


def test_sezioni_per_indirizzo():
    assert sezione_di("/pratiche/export.csv") == "esportazione"
    assert sezione_di("/pratiche/12") == "pratiche"
    assert sezione_di("/clienti/3/progetti/1") == "clienti"
    assert sezione_di("/") is None
    collaboratore = Utente(ruolo="collaboratore", sezioni=None)
    assert puo_vedere(collaboratore, "/clienti") and not puo_vedere(collaboratore, "/export")
    assert not puo_vedere(collaboratore, "/impostazioni/utenti")


def _entra(client, fabbrica, email, password):
    client.cookies.clear()
    r = client.post("/login", data={"email": email, "password": password})
    assert r.headers["location"] == "/2fa/configura"
    token = csrf(client, "/2fa/configura")
    r = client.post(
        "/2fa/configura", data={"codice": codice_totp(fabbrica, email), "csrf_token": token}
    )
    assert r.status_code == 200


def test_titolare_crea_utenti_con_sezioni(client, crea_utente, accedi, db, fabbrica):
    accedi(crea_utente())
    token = csrf(client, "/impostazioni/utenti")
    email = f"collab-{uuid.uuid4().hex[:6]}@studio.it"
    r = client.post(
        "/impostazioni/utenti",
        data={
            "csrf_token": token,
            "nome": "Luca Verdi",
            "email": email,
            "ruolo": "collaboratore",
            "sezioni": ["clienti", "bandi"],
        },
    )
    assert r.status_code == 200 and "Utente creato" in r.text
    password = re.search(r'id="password-temporanea">([^<]+)<', r.text).group(1)
    assert len(password) == 16
    u = db.scalar(select(Utente).where(Utente.email == email))
    assert u.sezioni == ["clienti", "bandi"] and u.ruolo == "collaboratore"
    # un'altra email uguale non si crea
    r = client.post(
        "/impostazioni/utenti",
        data={"csrf_token": token, "nome": "X", "email": email, "ruolo": "collaboratore"},
    )
    assert "Esiste+gi" in r.headers["location"] or "Esiste%20gi" in r.headers["location"]

    # Il collaboratore entra e vede solo le sue sezioni
    _entra(client, fabbrica, email, password)
    home = client.get("/").text
    assert 'href="/clienti"' in home and 'href="/bandi"' in home
    assert 'href="/pratiche"' not in home and 'href="/impostazioni/utenti"' not in home
    assert client.get("/clienti").status_code == 200
    assert client.get("/pratiche").status_code == 403
    assert client.get("/impostazioni/utenti").status_code == 403
    assert client.get("/export/completo.zip").status_code == 403


def test_sola_lettura_e_disattivazione(client, crea_utente, accedi, db, fabbrica):
    titolare = crea_utente()
    accedi(titolare)
    token = csrf(client, "/impostazioni/utenti")
    email = f"lettura-{uuid.uuid4().hex[:6]}@studio.it"
    r = client.post(
        "/impostazioni/utenti",
        data={
            "csrf_token": token,
            "nome": "Revisore",
            "email": email,
            "ruolo": "lettura",
            "sezioni": ["clienti"],
        },
    )
    password = re.search(r'id="password-temporanea">([^<]+)<', r.text).group(1)
    u = db.scalar(select(Utente).where(Utente.email == email))
    # il titolare non può disattivare sé stesso
    r = client.post(
        f"/impostazioni/utenti/{titolare.id}", data={"csrf_token": token, "azione": "disattiva"}
    )
    assert "Non+puoi" in r.headers["location"] or "Non%20puoi" in r.headers["location"]
    # disattiva e riattiva il revisore
    client.post(f"/impostazioni/utenti/{u.id}", data={"csrf_token": token, "azione": "disattiva"})
    db.refresh(u)
    assert not u.attivo
    client.post(f"/impostazioni/utenti/{u.id}", data={"csrf_token": token, "azione": "riattiva"})
    db.refresh(u)
    assert u.attivo

    # In sola lettura vede ma non modifica
    _entra(client, fabbrica, email, password)
    assert client.get("/clienti/nuovo").status_code == 200
    t2 = csrf(client, "/clienti/nuovo")
    assert (
        client.post("/clienti/nuovo", data={"csrf_token": t2, "ragione_sociale": "X"}).status_code
        == 403
    )
    assert client.get("/opportunita").status_code == 403  # sezione non assegnata
