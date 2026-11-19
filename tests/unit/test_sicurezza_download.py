# ruff: noqa: E501
"""Download sicuri: niente indirizzi interni (SSRF), rilettura delle sole pagine di gara,
link strani ignorati, permessi delle sezioni con negazione predefinita."""

import httpx
import pytest

from app.appalti import documenti as d
from app.auth.permessi import puo_vedere
from app.models import Appalto, Utente
from app.sources import http


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://ted.europa.eu/x",
        "https://ted.europa.eu:8443/x",
    ],
)
def test_indirizzi_interni_rifiutati(url):
    with pytest.raises(http.ErroreDownload):
        http.indirizzo_pubblico(url)


def test_redirect_verso_rete_interna_bloccato():
    def risposta(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pubblico.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        return httpx.Response(200, text="segreto")

    client = httpx.Client(
        transport=httpx.MockTransport(risposta),
        follow_redirects=True,
        event_hooks={"request": [http._controlla_richiesta]},
    )
    with pytest.raises(http.ErroreDownload):
        client.get("https://pubblico.example/gara")


def test_estrai_tiene_solo_link_web():
    pagina = (
        '<a href="ftp://x.example/bando.pdf">Bando</a>'
        '<a href="file:///c/disciplinare.pdf">Disciplinare</a>'
        '<a href="http://[::1/capitolato.pdf">Capitolato</a>'
        '<a href="/allegati/open.php?id=1">Domanda di partecipazione</a>'
    )
    trovati = d.estrai(pagina, "https://p.example/gare/1")
    assert [x["url"] for x in trovati] == ["https://p.example/allegati/open.php?id=1"]


def test_rilegge_solo_la_pagina_della_gara_mai_i_file():
    chieste: list[str] = []

    def risposta(request: httpx.Request) -> httpx.Response:
        chieste.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/allegati/open.php?id=2">Disciplinare</a>',
        )

    from app.documenti.servizio import Robots

    client = httpx.Client(transport=httpx.MockTransport(risposta))
    gara = Appalto(
        documenti=[
            {"titolo": "Bando", "url": "https://p.example/allegati/open.php?id=1", "tipo": "Bando di gara"},
            {"titolo": "Pagina", "url": "https://p.example/gare/dettaglio?id=9", "tipo": "Pagina della gara"},
        ]
    )  # fmt: skip
    assert d.leggi_gara(gara, client, Robots(client)) == "1 documenti"
    assert [u for u in chieste if "robots" not in u] == ["https://p.example/gare/dettaglio?id=9"]


def test_un_link_rotto_non_ferma_la_notte(monkeypatch):
    class Db:
        commit = rollback = lambda self: None  # noqa: E731

    rotta, buona = Appalto(id=1), Appalto(id=2)

    def leggi(a, client, robots):  # noqa: ANN001
        if a is rotta:
            raise ValueError("Invalid IPv6 URL")
        a.documenti_letti_il = "letta"
        return "2 documenti"

    monkeypatch.setattr(d, "leggi_gara", leggi)
    db = Db()
    db.scalars = lambda *_a, **_k: [rotta, buona]
    esito = d.aggiorna(db, client=httpx.Client())
    assert "2 lette, 1 con documenti" in esito
    assert rotta.documenti_letti_il is not None  # non si riprova ogni notte


def test_permessi_negati_per_indirizzi_non_mappati():
    collaboratore = Utente(ruolo="collaboratore", sezioni=["bandi", "clienti"])
    assert not puo_vedere(collaboratore, "/allegati/1")
    assert not puo_vedere(collaboratore, "/clienti/3/aggiornamenti")
    assert not puo_vedere(collaboratore, "/clienti/3/report/1")
    assert puo_vedere(collaboratore, "/clienti/3")
    assert not puo_vedere(collaboratore, "/qualcosa-di-nuovo")
    assert puo_vedere(collaboratore, "/account")
    assert puo_vedere(Utente(ruolo="titolare"), "/qualcosa-di-nuovo")
