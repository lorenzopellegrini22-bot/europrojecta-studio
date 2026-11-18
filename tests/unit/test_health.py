"""/health senza database: deve rispondere 503, non andare in errore."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_db
from app.main import app


class _SessioneRotta:
    def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connessione rifiutata"))


def test_health_database_non_raggiungibile():
    app.dependency_overrides[get_db] = lambda: _SessioneRotta()
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["database"] == "non raggiungibile"
