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
        fonte="ted", id_esterno="1-2026", link_partecipazione=None, documenti_letti_il=None, categorie_soa=None,
        documenti=[{"titolo": "Documenti di gara", "url": "https://p.example/gare/dettaglio.php?codice=9"}],
    )  # fmt: skip
    assert d.leggi_gara(gara, client, Robots(client)) == "2 documenti"
    assert gara.documenti[-1]["tipo"] == "Pagina della gara" and gara.documenti_letti_il
    vietata = N(
        fonte="ted", id_esterno="2-2026", link_partecipazione=None, documenti_letti_il=None, categorie_soa=None,
        documenti=[{"titolo": "Documenti di gara", "url": "https://p.example/vietato/gara?id=1"}],
    )  # fmt: skip
    assert "robots" in d.leggi_gara(vietata, client, Robots(client))


PAGINA_CON_DISCIPLINARE = """<html><body><h1>Lavori di efficientamento</h1>
<p>Categorie: OG 1, OS 30</p>
<a href="/vietato/bando.pdf">Bando di gara.pdf</a>
<a href="/files/disciplinare.pdf">Disciplinare di gara.pdf</a>
</body></html>"""


def test_prevalente_dal_disciplinare_rispettando_robots(monkeypatch):
    """La pubblicità legale ANAC elenca OG1 e OS30 senza dire qual è la prevalente: la si
    legge nel disciplinare in PDF (il bando, vietato dal robots.txt, non si apre)."""
    from app.documenti import servizio
    from app.documenti.servizio import Robots

    letti: list[str] = []

    def risposta(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /vietato/")
        if req.url.path.endswith(".pdf"):
            letti.append(req.url.path)
            return httpx.Response(200, content=b"%PDF-1.7 disciplinare",
                                  headers={"content-type": "application/pdf"})  # fmt: skip
        return httpx.Response(200, html=PAGINA_CON_DISCIPLINARE)

    testo = (
        "3. CATEGORIE DEI LAVORI\nLa categoria prevalente è la OS 30 (impianti elettrici), "
        "classifica III, € 900.000,00; per la categoria OG 1 € 300.000,00 (scorporabile).\n"
        "Esempio: le categorie OS 4 e OG 11 non sono richieste."
    )
    monkeypatch.setattr(servizio, "testo_pdf", lambda dati, *a, **k: (testo, 3))
    client = httpx.Client(transport=httpx.MockTransport(risposta))
    gara = N(
        fonte="anac", id_esterno="pvl-1", link_partecipazione=None, documenti_letti_il=None,
        documenti=[{"titolo": "Documenti di gara", "url": "https://p.example/gare/dettaglio.php?codice=9"}],
        categorie_soa=[{"codice": "OG1", "tipo": None, "provenienza": "anac"},
                       {"codice": "OS30", "tipo": None, "provenienza": "anac"}],
        codici_soa=["OG1", "OS30"],
    )  # fmt: skip
    assert d.manca_prevalente(gara)
    assert d.leggi_gara(gara, client, Robots(client)) == "2 documenti, prevalente dal disciplinare"
    assert letti == ["/files/disciplinare.pdf"]
    tipi = {c["codice"]: c["tipo"] for c in gara.categorie_soa}
    assert tipi == {"OS30": "prevalente", "OG1": "scorporabile"}  # niente OS4/OG11 di esempio
    assert gara.codici_soa == ["OG1", "OS30"] and not d.manca_prevalente(gara)


def test_prevalente_nel_testo_della_pagina():
    gara = N(categorie_soa=[{"codice": "OG3", "tipo": None, "provenienza": "anac"},
                            {"codice": "OS12-A", "tipo": None, "provenienza": "anac"}],
             codici_soa=["OG3", "OS12-A"])  # fmt: skip
    assert not d.categorie_dal_testo(gara, "Categorie: OG3 e OS12-A. Importo € 500.000")
    assert d.categorie_dal_testo(gara, "Categoria prevalente: OG 3 classifica II")
    assert d.manca_prevalente(gara) is False
