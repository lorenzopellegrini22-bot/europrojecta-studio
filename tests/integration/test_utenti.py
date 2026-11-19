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

    # Il collaboratore entra: prima deve cambiare la password temporanea
    _entra(client, fabbrica, email, password)
    r = client.get("/clienti", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account?obbligo=1"
    assert "password temporanea" in client.get("/account").text
    t_acc = csrf(client, "/account")
    nuova = "una-password-tutta-mia-2026"
    r = client.post(
        "/account/password",
        data={"csrf_token": t_acc, "attuale": password, "nuova": nuova, "conferma": nuova},
    )
    assert r.status_code == 303
    # ora vede solo le sue sezioni
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

    # In sola lettura vede ma non modifica (dopo aver cambiato la password temporanea)
    _entra(client, fabbrica, email, password)
    t_acc = csrf(client, "/account")
    nuova = "una-password-tutta-mia-2026"
    client.post(
        "/account/password",
        data={"csrf_token": t_acc, "attuale": password, "nuova": nuova, "conferma": nuova},
    )
    assert client.get("/clienti/nuovo").status_code == 200
    t2 = csrf(client, "/clienti/nuovo")
    assert (
        client.post("/clienti/nuovo", data={"csrf_token": t2, "ragione_sociale": "X"}).status_code
        == 403
    )
    assert client.get("/opportunita").status_code == 403  # sezione non assegnata


def test_invito_via_email(client, crea_utente, accedi, db, fabbrica, monkeypatch):
    from tests.integration.test_funzioni_studio import _configura_smtp

    accedi(crea_utente())
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)),
    )
    token = csrf(client, "/impostazioni/utenti")
    email = f"invitato-{uuid.uuid4().hex[:6]}@studio.it"
    r = client.post(
        "/impostazioni/utenti",
        data={
            "csrf_token": token,
            "nome": "Sara Neri",
            "email": email,
            "ruolo": "collaboratore",
            "modo": "invito",
            "sezioni": ["clienti"],
        },
    )
    assert (
        r.status_code == 303
        and "Invito+inviato" in r.headers["location"]
        or "Invito%20inviato" in r.headers["location"]
    )
    a, oggetto, testo = next(x for x in inviate if x[0] == email)
    assert "password" not in oggetto.lower() or "Il tuo accesso" in oggetto
    link = re.search(r"https?://\S+/invito/(\S+)", testo)
    assert link, testo
    tok = link.group(1)
    u = db.scalar(select(Utente).where(Utente.email == email))
    assert u.invito_hash and tok not in (u.invito_hash or "")  # nel database solo l'impronta

    client.cookies.clear()
    assert "Benvenuto" in client.get(f"/invito/{tok}").text
    r = client.post(f"/invito/{tok}", data={"nuova": "corta", "conferma": "corta"})
    assert r.status_code == 400
    nuova = "password-scelta-da-sara"
    r = client.post(f"/invito/{tok}", data={"nuova": nuova, "conferma": nuova})
    assert r.headers["location"] == "/login?invito=1"
    assert client.get(f"/invito/{tok}").status_code == 404  # monouso
    db.refresh(u)
    assert not u.deve_cambiare_password and u.invito_hash is None
    # entra con la sua password e configura la 2FA, senza obbligo di cambio
    _entra(client, fabbrica, email, nuova)
    assert client.get("/clienti").status_code == 200
