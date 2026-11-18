# ruff: noqa: E501
"""Documentazione delle gare: lettura delle pagine delle piattaforme (esempi ridotti dai
formati reali di Asmecomm e delle piattaforme «DettaglioProcedura.asp»)."""

from types import SimpleNamespace as N

import httpx

from app.appalti import documenti as d

ASMECOMM = """<html><body>
<a href="/allegati/open.php?codice=1&filename=YS1iYW5kby5wZGY=">a-bando-di-gara.pdf</a>
<a href="/allegati/open.php?codice=2&filename=Yi1kaXNj">b-disciplinare-di-gara.pdf</a>
<a href="/allegati/verifica/form.php">Verifica documento</a>
<a href="/gare/elenco.php">Torna all'elenco</a>
</body></html>"""
ARIA = """<label onclick="window.open('https://sardegnacat.example/application/CTL_Library/functions/field/DisplayAttach.ASP?OPERATION=DISPLAY&amp;FIELD=Allegato&amp;TECHVALUE=' + escape('Disciplinare di gara.pdf*pdf*135506*abc') + '&amp;FORMAT=I');">Disciplinare</label>"""


def test_estrae_link_diretti_e_javascript():
    trovati = d.estrai(ASMECOMM, "https://piattaforma.asmecomm.it/gare/dettaglio.php?codice=1")
    assert [x["tipo"] for x in trovati] == ["Bando di gara", "Disciplinare"]
    assert trovati[0]["url"].startswith("https://piattaforma.asmecomm.it/allegati/open.php")
    aria = d.estrai(ARIA, "https://sardegnacat.example/x")
    assert aria[0]["titolo"] == "Disciplinare di gara.pdf" and aria[0]["tipo"] == "Disciplinare"
    assert "TECHVALUE=Disciplinare%20di%20gara.pdf*pdf*135506*abc&FORMAT=I" in aria[0]["url"]


def test_tipi_pagine_e_bando_guue():
    assert (
        d.tipo_documento("A1 - Domanda di partecipazione rev.03.docx")
        == "Domanda di partecipazione"
    )
    assert (
        d.tipo_documento("ALL. D Modello presentazione offerta economica")
        == "Offerta economica e tecnica"
    )
    assert d.tipo_documento("Foto del sito.jpg") == "Altri allegati"
    assert d.pagina_specifica("https://piattaforma.asmecomm.it/gare/dettaglio.php?codice=1")
    assert not d.pagina_specifica("https://www.sintel.regione.lombardia.it")
    assert not d.pagina_specifica(
        "https://webs.rete.toscana.it/sitat/trasparenza/#/procedura?uuid=x"
    )
    gara = N(fonte="ted", id_esterno="400184-2024", documenti=None)
    assert d.pdf_ted(gara) == "https://ted.europa.eu/it/notice/400184-2024/pdf"
    assert d.documentazione(gara)[0]["tipo"] == "Bando GUUE"


def test_legge_la_pagina_rispettando_robots():
    def risposta(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /vietato/")
        return httpx.Response(200, html=ASMECOMM)

    from app.documenti.servizio import Robots

    client = httpx.Client(transport=httpx.MockTransport(risposta))
    gara = N(
        fonte="ted", id_esterno="1-2026", link_partecipazione=None, documenti_letti_il=None,
        documenti=[{"titolo": "Documenti di gara", "url": "https://p.example/gare/dettaglio.php?codice=9"}],
    )  # fmt: skip
    assert d.leggi_gara(gara, client, Robots(client)) == "2 documenti"
    assert gara.documenti[-1]["tipo"] == "Pagina della gara" and gara.documenti_letti_il
    vietata = N(
        fonte="ted", id_esterno="2-2026", link_partecipazione=None, documenti_letti_il=None,
        documenti=[{"titolo": "Documenti di gara", "url": "https://p.example/vietato/gara?id=1"}],
    )  # fmt: skip
    assert "robots" in d.leggi_gara(vietata, client, Robots(client))
