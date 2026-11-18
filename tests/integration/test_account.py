import re

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, Sessione
from tests.integration.conftest import PASSWORD, codice_totp, csrf

pytestmark = pytest.mark.integration

NUOVA = "una-nuova-password-sicura"


def test_pagina_account(client, crea_utente, accedi):
    u = crea_utente()
    accedi(u)
    r = client.get("/account")
    assert r.status_code == 200
    assert u.email in r.text
    assert "Ti restano <strong>10</strong>" in r.text
    assert "Accesso riuscito" in r.text


def test_cambio_password(client, crea_utente, accedi, db):
    u = crea_utente()
    accedi(u)
    # una seconda sessione "su un altro dispositivo"
    altra = Sessione(
        token_hash="x" * 64,
        csrf_token="y",
        utente_id=u.id,
        mfa_verificata=True,
        scade_il=db.scalar(select(Sessione.scade_il).where(Sessione.utente_id == u.id)),
        ultimo_uso=db.scalar(select(Sessione.ultimo_uso).where(Sessione.utente_id == u.id)),
    )
    db.add(altra)
    db.commit()

    token = csrf(client, "/account")
    r = client.post(
        "/account/password",
        data={"attuale": "sbagliata", "nuova": NUOVA, "conferma": NUOVA, "csrf_token": token},
    )
    assert r.status_code == 400 and "attuale non è corretta" in r.text

    r = client.post(
        "/account/password",
        data={"attuale": PASSWORD, "nuova": NUOVA, "conferma": NUOVA, "csrf_token": token},
    )
    assert r.status_code == 303
    db.expire_all()
    sessioni = db.scalars(select(Sessione).where(Sessione.utente_id == u.id)).all()
    assert len(sessioni) == 1  # gli altri dispositivi sono stati disconnessi
    assert client.get("/account").status_code == 200  # questo dispositivo resta collegato
    voce = db.scalars(select(AuditLog).where(AuditLog.entita_id == str(u.id))).all()[-1]
    assert voce.differenze == {"password": "modificata"}

    client.post("/logout", data={"csrf_token": csrf(client, "/account")})
    r = client.post("/login", data={"email": u.email, "password": NUOVA})
    assert r.headers["location"] == "/login/2fa"


def test_nuovi_codici_di_recupero(client, crea_utente, accedi, fabbrica):
    u = crea_utente()
    vecchi = accedi(u)
    token = csrf(client, "/account")
    r = client.post("/account/codici-recupero", data={"codice": "000000", "csrf_token": token})
    assert r.status_code == 400
    r = client.post(
        "/account/codici-recupero",
        data={"codice": codice_totp(fabbrica, u.email, scarto=1), "csrf_token": token},
    )
    assert r.status_code == 200
    nuovi = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", r.text)
    assert len(nuovi) == 10 and not set(nuovi) & set(vecchi)


def test_limite_per_ip_su_email_diverse(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_tentativi_ip", 5)
    for i in range(5):
        r = client.post("/login", data={"email": f"x{i}@altro.it", "password": "x" * 12})
        assert "Email o password non corretti" in r.text
    r = client.post("/login", data={"email": "ancora@altro.it", "password": "x" * 12})
    assert "Troppi tentativi da questo indirizzo" in r.text
