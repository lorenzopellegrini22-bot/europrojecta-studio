"""«Password dimenticata»: link via email, monouso, valido un'ora; la pagina non rivela chi
ha un accesso; dopo il cambio le sessioni aperte si chiudono."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.auth import recupero
from app.auth.password import verifica_password
from app.models import LogAccesso, Sessione, Utente
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture
def inviate(client, crea_utente, accedi, db, monkeypatch):
    from tests.integration.test_funzioni_studio import _configura_smtp

    accedi(crea_utente())
    _configura_smtp(client)
    client.cookies.clear()
    db.execute(delete(LogAccesso).where(LogAccesso.esito == "recupero_password"))
    db.commit()
    posta: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: posta.append((a, oggetto, testo)),
    )
    return posta


def _chiedi(client, email: str) -> str:
    r = client.post("/password-dimenticata", data={"email": email})
    assert r.status_code == 200
    return r.text


def test_link_via_email_e_nuova_password(client, crea_utente, db, inviate):
    u = crea_utente("collaboratore")
    adesso = datetime.now(UTC)
    db.add(Sessione(utente_id=u.id, token_hash=uuid.uuid4().hex * 2, csrf_token="c",
                    mfa_verificata=True, scade_il=adesso + timedelta(hours=1),
                    ultimo_uso=adesso))  # fmt: skip
    u.tentativi_falliti = 3
    db.commit()
    assert 'href="/password-dimenticata"' in client.get("/login").text

    # stessa risposta per un'email registrata e una sconosciuta
    risposta = _chiedi(client, u.email.upper())
    sconosciuta = _chiedi(client, "nessuno@studio.it")
    assert "tra poco riceverà" in risposta and "tra poco riceverà" in sconosciuta
    assert [a for a, _, _ in inviate] == [u.email]
    _, oggetto, testo = inviate[0]
    token = re.search(r"/password-dimenticata/(\S+)", testo).group(1)
    db.expire_all()
    u = db.get(Utente, u.id)
    assert u.recupero_hash and token not in u.recupero_hash  # nel database solo l'impronta

    # una seconda richiesta subito dopo non manda un altro link
    _chiedi(client, u.email)
    assert len(inviate) == 1

    assert "Nuova password" in client.get(f"/password-dimenticata/{token}").text
    r = client.post(f"/password-dimenticata/{token}", data={"nuova": "corta", "conferma": "corta"})
    assert r.status_code == 400
    nuova = "password-nuova-di-mario"
    r = client.post(f"/password-dimenticata/{token}", data={"nuova": nuova, "conferma": nuova})
    assert r.status_code == 303 and r.headers["location"] == "/login?reimpostata=1"
    db.expire_all()
    u = db.get(Utente, u.id)
    assert verifica_password(u.password_hash, nuova)
    assert not verifica_password(u.password_hash, PASSWORD)
    assert u.recupero_hash is None and u.tentativi_falliti == 0
    assert db.scalar(select(Sessione).where(Sessione.utente_id == u.id)) is None
    # il link si usa una volta sola
    assert client.get(f"/password-dimenticata/{token}").status_code == 404
    r = client.post(f"/password-dimenticata/{token}", data={"nuova": nuova, "conferma": nuova})
    assert r.status_code == 404


def test_link_scaduto_e_limite_per_indirizzo(client, crea_utente, db, inviate, monkeypatch):
    u = crea_utente()
    _chiedi(client, u.email)
    token = re.search(r"/password-dimenticata/(\S+)", inviate[0][2]).group(1)
    db.expire_all()
    u = db.get(Utente, u.id)
    u.recupero_scade_il = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert "Link non valido" in client.get(f"/password-dimenticata/{token}").text

    monkeypatch.setattr(recupero, "ATTESA_TRA_INVII", timedelta(0))
    for _ in range(recupero.MAX_RICHIESTE_IP):
        _chiedi(client, u.email)
    # oltre il limite la pagina risponde uguale ma non manda nulla
    assert len(inviate) == recupero.MAX_RICHIESTE_IP
    assert "tra poco riceverà" in _chiedi(client, u.email)
    assert len(inviate) == recupero.MAX_RICHIESTE_IP
    # le richieste non contano come accessi falliti: il login da qui funziona ancora
    r = client.post("/login", data={"email": u.email, "password": PASSWORD})
    assert r.status_code == 303


def test_senza_email_configurata_nessun_link_valido(client, crea_utente, db, inviate, monkeypatch):
    monkeypatch.setattr(
        "app.comunicazioni.invia_registrando", lambda *a, **k: (False, "non configurata")
    )
    u = crea_utente()
    assert "tra poco riceverà" in _chiedi(client, u.email)
    db.expire_all()
    assert db.get(Utente, u.id).recupero_hash is None
