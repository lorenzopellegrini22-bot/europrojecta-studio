"""Fonti regionali: parser degli elenchi e dei pacchetti, con dati reali ridotti (18/11/2026)."""

import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.bandi.stato import calcola_stato
from app.sources import http
from app.sources.regioni import comune, lazio, toscana
from app.sources.regioni.comune import ErroreFormato

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _pacchetto(nome: str, tmp_path: Path) -> Path:
    p = tmp_path / nome
    shutil.copy(FIXTURES / nome, p)
    return p


# --- funzioni comuni -------------------------------------------------------------------


def test_data_italiana():
    assert comune.data_italiana(": ore 16:00 del 18/12/2026.") == date(2026, 12, 18)
    assert comune.data_italiana("Domande dal: 11 Maggio 2026 10:00") == date(2026, 5, 11)
    assert comune.data_italiana("dal 2 novembre 2026 al 30/11/2026") == date(2026, 11, 2)
    assert comune.data_italiana("31/02/2026") is None
    assert comune.data_italiana(None) is None


def test_testo_da_html():
    assert comune.testo_da_html("<p>Uno&nbsp;&amp;  due</p><script>x()</script><p>tre</p>") == (
        "Uno & due\ntre"
    )


def test_mappa_e_classi():
    post = {"class_list": ["bandi", "destinatari-pmi", "destinatari-sconosciuto", "tema-energia"]}
    assert comune.classi(post, "destinatari") == ["pmi", "sconosciuto"]
    assert comune.mappa(["pmi", "sconosciuto"], {"pmi": ["Impresa"]}) == ["Impresa"]
    assert comune.mappa(["sconosciuto"], {"pmi": ["Impresa"]}) is None


def test_stato_dichiarato_dalla_fonte():
    oggi = date(2026, 11, 18)
    assert calcola_stato(None, None, oggi, stato_fonte="aperto") == "aperto"
    assert calcola_stato(None, None, oggi, stato_fonte="in_arrivo") == "in_arrivo"
    assert calcola_stato(None, None, oggi) == "sconosciuto"
    # le date prevalgono, tranne "chiuso" dichiarato (risorse esaurite)
    assert calcola_stato(None, date(2026, 11, 1), oggi, stato_fonte="aperto") == "chiuso"
    assert calcola_stato(None, date(2026, 12, 1), oggi, stato_fonte="chiuso") == "chiuso"


def test_redirect_ad_altro_dominio_bloccato():
    """Un reindirizzamento a una verifica anti-bot ferma la fonte invece di aggirarla."""

    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.host == "bandi.esempio.it":
            return httpx.Response(302, headers={"location": "https://verifica.esempio.com/"})
        return httpx.Response(200, text="captcha")

    trasporto = httpx.MockTransport(gestore)
    with (
        httpx.Client(transport=trasporto, follow_redirects=True) as c,
        pytest.raises(http.ErroreDownload, match="reindirizzato"),
    ):
        http.pagina(c, "https://bandi.esempio.it/elenco")


# --- Lazio -----------------------------------------------------------------------------

ELENCO_LAZIO_INNOVA = """
<select name="stati"><option value="aperto">Aperto</option></select>
<article class="post-1 bandi type-bandi"><h2 class="entry-title">
<a href="https://www.lazioinnova.it/bandi/primo-bando/">Primo</a></h2></article>
<article class="post-2 bandi type-bandi"><h2 class="entry-title">
<a href="https://www.lazioinnova.it/bandi/secondo-bando/">Secondo</a></h2></article>
<a href="https://www.lazioinnova.it/bandi/page/2/?stati=aperto">2</a>
"""

ELENCO_LAZIO_EUROPA = """
<select name="stati"><option value="1">Aperto</option></select>
<article class="box-bandi"><div class="box-bandi-titolo">
<a href="https://www.lazioeuropa.it/bandi/terzo-bando/">Terzo</a></div></article>
<a href="https://www.lazioeuropa.it/bandi/page/2/?stati=1">2</a>
<a href="https://www.lazioeuropa.it/bandi/page/4/?stati=1">4</a>
"""


def test_elenchi_lazio():
    assert lazio.elenco_lazio_innova(ELENCO_LAZIO_INNOVA) == (["primo-bando", "secondo-bando"], [2])
    assert lazio.elenco_lazio_europa(ELENCO_LAZIO_EUROPA) == (["terzo-bando"], [2, 4])
    with pytest.raises(ErroreFormato):
        lazio.elenco_lazio_innova("<html><body>Pagina cambiata</body></html>")


def test_pacchetto_lazio(tmp_path):
    record = lazio.FONTE.interpreta(_pacchetto("regione_lazio_pacchetto.json", tmp_path))
    per_titolo = {r.titolo: r for r in record}
    assert len(record) == 4

    voucher = per_titolo["Voucher Internazionalizzazione Pmi 2027"]
    assert voucher.id_esterno.startswith("lazioinnova-")
    assert voucher.stato_fonte == "in_arrivo"
    assert voucher.ente == "Regione Lazio - Lazio Innova"
    assert voucher.requisiti.territorio == "regionale"
    assert voucher.requisiti.regioni == ["Lazio"]
    assert voucher.requisiti.dimensioni == ["piccola", "media"]
    assert voucher.requisiti.forma_agevolazione == ["Contributo/Fondo perduto"]
    assert voucher.requisiti.finalita == ["Internazionalizzazione"]
    assert "date" in voucher.requisiti.da_verificare
    assert "Fiere Internazionali" in (voucher.descrizione or "")

    europa = [r for r in record if r.id_esterno.startswith("lazioeuropa-")]
    assert len(europa) == 2
    assert all(r.ente == "Regione Lazio" for r in europa)  # nome dalla tassonomia "enti"
    assert all(r.requisiti.dimensioni is None for r in europa)  # scuole e cittadini


# --- Toscana ---------------------------------------------------------------------------

ELENCO_TOSCANA = """
<div class="facetwp-facet facetwp-facet-stato" data-name="stato"></div>
<a href="https://www.sviluppo.toscana.it/bando/uno/">Uno</a>
<a href="https://www.sviluppo.toscana.it/bando/uno/">Uno (ancora)</a>
<a href="https://www.sviluppo.toscana.it/bando/due/">Due</a>
<script>var FWP_JSON = {"pager":{"page":1,"per_page":12,"total_rows":41,"total_pages":4}};</script>
"""


def test_elenco_toscana():
    assert toscana.elenco(ELENCO_TOSCANA) == (["uno", "due"], 4)
    with pytest.raises(ErroreFormato):
        toscana.elenco("<html>nessun filtro</html>")


def test_pagina_toscana():
    html = """<main><h1>Bando Prova</h1><p>Stato:</p><p>Aperto</p>
    <p>Domande dal:</p><p>18 Novembre 2026 10:00</p>
    <p>Apertura</p><p>: ore 10:00 del 18/11/2026.</p>
    <p>Chiusura</p><p>: ore 16:00 del 18/12/2026.</p>
    </main>"""
    d = toscana.dettagli_pagina(html)
    assert d["titolo"] == "Bando Prova"
    assert d["stato"].startswith("aperto")
    assert comune.data_italiana(d["chiusura"]) == date(2026, 12, 18)


def test_pacchetto_toscana(tmp_path):
    record = toscana.FONTE.interpreta(_pacchetto("regione_toscana_pacchetto.json", tmp_path))
    per_titolo = {r.titolo: r for r in record}
    assert len(record) == 3

    inter = per_titolo["Bando Internazionalizzazione 2026"]
    assert inter.data_apertura == date(2026, 11, 18)
    assert inter.data_chiusura == date(2026, 12, 18)
    assert inter.stato_fonte == "aperto"
    assert inter.requisiti.regioni == ["Toscana"]
    assert inter.requisiti.dimensioni == ["micro", "piccola", "media"]
    assert inter.requisiti.finalita == ["Internazionalizzazione"]
    assert "date" not in inter.requisiti.da_verificare

    energia = per_titolo["Bando Energia Imprese processi produttivi"]
    assert energia.stato_fonte == "in_arrivo"
    assert calcola_stato(None, None, date(2026, 11, 18), stato_fonte=energia.stato_fonte) == (
        "in_arrivo"
    )


def test_pacchetto_non_valido(tmp_path):
    p = tmp_path / "rotto.json"
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ErroreFormato):
        toscana.FONTE.interpreta(p)
