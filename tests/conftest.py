"""Fixture comuni.

I test di integrazione usano un PostgreSQL reale con pgvector. TEST_DATABASE_URL punta a
un database "di servizio" (es. postgres): ogni esecuzione crea un database con nome casuale,
applica le migrazioni e lo elimina alla fine, così esecuzioni parallele non si disturbano.
Senza TEST_DATABASE_URL i test di integrazione vengono saltati (in CI è sempre impostata).
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

# Valori minimi per poter importare l'app anche nei test unitari.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/non_usato")
os.environ.setdefault("SECRET_KEY", "chiave-di-test-lunga-almeno-trentadue-caratteri")
os.environ.setdefault("AMBIENTE", "test")
# Il TestClient lavora su http://testserver: con Secure=true non rimanderebbe il cookie.
os.environ["COOKIE_SECURE"] = "false"
# Nessuna chiave reale deve finire nei test.
for _var in ("OPENAI_API_KEY", "SMTP_HOST", "SMTP_PASSWORD", "WHATSAPP_ACCESS_TOKEN"):
    os.environ.pop(_var, None)


def _url_servizio() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL non impostata: test di integrazione saltati")
    return url


@pytest.fixture(scope="session")
def url_db_test() -> Iterator[str]:
    from alembic import command
    from alembic.config import Config

    servizio = make_url(_url_servizio())
    nome = f"test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(servizio, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nome}"'))
    url = servizio.set(database=nome).render_as_string(hide_password=False)
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def engine_test(url_db_test: str) -> Iterator[Engine]:
    """Engine del database di test. Anche il codice che apre sessioni da solo (job, CLI)
    deve usare questo database: si punta DATABASE_URL qui e si svuotano le cache."""
    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    os.environ["DATABASE_URL"] = url_db_test
    for funzione in (get_settings, get_engine, get_sessionmaker):
        funzione.cache_clear()
    engine = create_engine(url_db_test)
    yield engine
    engine.dispose()
    get_engine().dispose()
