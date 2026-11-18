import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.jobs.lock import lock_job
from app.main import app

pytestmark = pytest.mark.integration


def test_estensioni_installate(engine_test: Engine):
    with engine_test.connect() as conn:
        estensioni = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
        assert "vector" in estensioni
        # pgvector funziona davvero: distanza coseno tra due vettori
        distanza = conn.execute(text("SELECT '[1,0]'::vector <=> '[0,1]'::vector")).scalar()
        assert distanza == pytest.approx(1.0)


def test_health_con_database_reale(engine_test: Engine):
    fabbrica = sessionmaker(bind=engine_test)

    def db_test():
        with fabbrica() as s:
            yield s

    app.dependency_overrides[get_db] = db_test
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json() == {"stato": "ok", "database": "ok"}


def test_lock_impedisce_esecuzioni_sovrapposte(engine_test: Engine):
    with lock_job(engine_test, "job_di_prova") as primo:
        assert primo is True
        with lock_job(engine_test, "job_di_prova") as secondo:
            assert secondo is False
    # rilasciato: si può riprendere
    with lock_job(engine_test, "job_di_prova") as terzo:
        assert terzo is True
