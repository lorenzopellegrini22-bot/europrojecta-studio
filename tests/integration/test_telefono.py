"""Uso da telefono e web app (verifica del 22/11/2026 a 375px di larghezza)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.appalti import Appalto
from app.models.opportunita import Opportunita
from tests.integration.conftest import csrf
from tests.integration.test_pratiche import _cliente

pytestmark = pytest.mark.integration


def test_service_worker_manifest_e_cache_dei_file_statici(client):
    # pubblici: il telefono li chiede anche prima dell'accesso
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert sw.headers["cache-control"] == "no-cache" and "/static/" in sw.text
    m = client.get("/manifest.webmanifest").json()
    assert m["display"] == "standalone" and m["id"] == "/oggi"
    # file con la versione nell'indirizzo: un anno; senza versione: un giorno
    css = client.get("/static/css/app.css?v=abc")
    assert css.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/img/icona-192.png").headers["cache-control"] == (
        "public, max-age=86400"
    )
    # le pagine dell'app non si salvano mai
    assert client.get("/login").headers["cache-control"] == "no-store"


def test_barra_in_basso_secondo_i_permessi(client, crea_utente, accedi):
    accedi(crea_utente())
    pagina = client.get("/oggi").text
    assert 'class="barra-basso"' in pagina
    for voce in ("/oggi", "/clienti", "/opportunita", "/scadenzario", "/cerca"):
        assert f'<a href="{voce}"' in pagina


def test_opportunita_a_pagine_con_un_solo_modulo(client, crea_utente, accedi, db, monkeypatch):
    """Con 355 opportunità la pagina pesava 1 MB: ora 100 per pagina, un modulo per
    opportunità (i pulsanti scelgono l'indirizzo con formaction) e la documentazione si
    carica all'apertura della tendina."""
    from app.opportunita import router

    monkeypatch.setattr(router, "PER_PAGINA", 2)
    accedi(crea_utente())
    azienda = _cliente(client, db)
    gare = []
    for i in range(3):
        a = Appalto(
            fonte="ted", id_esterno=f"tel-{uuid.uuid4().hex[:8]}", titolo=f"Gara telefono {i}",
            scadenza=date.today() + timedelta(days=30), raw={},
            documenti=[{"titolo": "Disciplinare di gara", "url": "https://ente.example/d.pdf"}],
        )  # fmt: skip
        db.add(a)
        db.flush()
        gare.append(a)
        db.add(
            Opportunita(
                azienda_id=azienda.id, appalto_id=a.id, punteggio=90 - i, motivi={},
                stato="nuova", trovata_il=datetime.now(UTC),
            )
        )  # fmt: skip
    db.commit()

    prima = client.get(f"/opportunita?cliente={azienda.id}").text
    assert "Gara telefono 0" in prima and "Gara telefono 2" not in prima
    assert "Pagina 1 di 2" in prima and f"cliente={azienda.id}&pagina=2" in prima
    assert prima.count('class="gruppo-pulsanti"') == 2
    assert 'formaction="/opportunita/' in prima and "Disciplinare di gara" not in prima
    seconda = client.get(f"/opportunita?cliente={azienda.id}&pagina=2").text
    assert "Gara telefono 2" in seconda and "Pagina 2 di 2" in seconda
    # pagina oltre l'ultima: si mostra l'ultima
    assert "Gara telefono 2" in client.get(f"/opportunita?cliente={azienda.id}&pagina=9").text

    o = db.scalar(select(Opportunita).where(Opportunita.appalto_id == gare[0].id))
    frammento = client.get(f"/opportunita/{o.id}/documentazione").text
    assert "Disciplinare di gara" in frammento and "<html" not in frammento

    # i pulsanti del modulo unico: feedback e stato con gli stessi campi nascosti
    token = csrf(client, f"/opportunita?cliente={azienda.id}")
    torna = f"/opportunita?cliente={azienda.id}"
    r = client.post(f"/opportunita/{o.id}/feedback",
                    data={"csrf_token": token, "torna": torna, "feedback": "utile"})  # fmt: skip
    assert r.status_code == 303 and r.headers["location"] == torna
    r = client.post(f"/opportunita/{o.id}/stato",
                    data={"csrf_token": token, "torna": torna, "stato": "scartata"})  # fmt: skip
    assert r.status_code == 303
    db.expire_all()
    o = db.get(Opportunita, o.id)
    assert o.feedback == "utile" and o.stato == "scartata"
