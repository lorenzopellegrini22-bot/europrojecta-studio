import re
import time
import uuid
from collections.abc import Iterator

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.password import hash_password
from app.auth.servizio import segreto_totp
from app.db import get_db
from app.main import app
from app.models import Utente

PASSWORD = "password-di-prova-lunga"


@pytest.fixture(scope="session")
def fabbrica(engine_test: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine_test, expire_on_commit=False)


@pytest.fixture
def db(fabbrica: sessionmaker[Session]) -> Iterator[Session]:
    with fabbrica() as s:
        yield s


@pytest.fixture
def client(fabbrica: sessionmaker[Session]) -> Iterator[TestClient]:
    def db_test() -> Iterator[Session]:
        with fabbrica() as s:
            yield s

    app.dependency_overrides[get_db] = db_test
    with TestClient(app, follow_redirects=False, headers={"Origin": "http://testserver"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def crea_utente(db: Session):
    def _crea(ruolo: str = "titolare") -> Utente:
        u = Utente(
            email=f"utente-{uuid.uuid4().hex[:8]}@studio.it",
            nome="Mario Rossi",
            password_hash=hash_password(PASSWORD),
            ruolo=ruolo,
        )
        db.add(u)
        db.commit()
        return u

    return _crea


def csrf(client: TestClient, url: str) -> str:
    """Legge il token CSRF dalla pagina (campo nascosto del form)."""
    html = client.get(url).text
    marcatore = 'name="csrf_token" value="'
    inizio = html.index(marcatore) + len(marcatore)
    return html[inizio : html.index('"', inizio)]


def codice_totp(fabbrica: sessionmaker[Session], email: str, scarto: int = 0) -> str:
    with fabbrica() as s:
        u = s.scalar(select(Utente).where(Utente.email == email))
        segreto = segreto_totp(u)
    assert segreto
    return pyotp.TOTP(segreto).at(time.time() + 30 * scarto)


@pytest.fixture
def accedi(client: TestClient, fabbrica: sessionmaker[Session]):
    """Login completo (password + configurazione 2FA). Restituisce i codici di recupero."""

    def _accedi(utente: Utente) -> list[str]:
        r = client.post("/login", data={"email": utente.email, "password": PASSWORD})
        assert r.status_code == 303 and r.headers["location"] == "/2fa/configura"
        token = csrf(client, "/2fa/configura")
        r = client.post(
            "/2fa/configura",
            data={"codice": codice_totp(fabbrica, utente.email), "csrf_token": token},
        )
        assert r.status_code == 200, r.text
        return re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", r.text)

    return _accedi
