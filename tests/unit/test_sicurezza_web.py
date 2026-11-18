"""Controlli del middleware che non richiedono il database."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_post_senza_origin_rifiutato():
    r = client.post("/login", data={"email": "a@b.it", "password": "x"})
    assert r.status_code == 403


def test_post_da_altra_origine_rifiutato():
    r = client.post(
        "/login",
        data={"email": "a@b.it", "password": "x"},
        headers={"Origin": "https://sito-malevolo.example"},
    )
    assert r.status_code == 403


def test_header_di_sicurezza():
    r = client.get("/static/css/app.css")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
