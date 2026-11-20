from app.bandi.doppioni import parole, regione_della_fonte, somiglianza
from app.models import Fonte


def test_somiglianza_titoli():
    assert somiglianza("Basket Bond Lazio", "BASKET BOND LAZIO") == 1.0
    a = "Basket Bond Lazio – Contributo sui costi di emissione sostenuti dalle PMI"
    b = "Contributo sui costi di emissione sostenuti dalle PMI ammissibili"
    assert somiglianza(a, b) >= 0.6
    # titoli vicini ma bandi diversi (misurati sui dati reali): sotto la soglia
    assert (
        somiglianza(
            "Voucher Internazionalizzazione Pmi 2027",
            "Bando Voucher Internazionalizzazione 2026 - CCIAA Potenza",
        )
        < 0.6
    )
    assert "bando" not in parole("Bando per la digitalizzazione")


def test_regione_della_fonte():
    assert (
        regione_della_fonte(Fonte(codice="regione_toscana", nome="Regione Toscana", url="x"))
        == "Toscana"
    )
    assert (
        regione_della_fonte(Fonte(codice="incentivi_italia", nome="Incentivi.gov.it", url="x"))
        is None
    )


def test_stessa_pagina_ufficiale():
    from app.bandi.doppioni import pagina

    # lo stesso bando, link scritti in modo diverso dalla Regione e dalla fonte nazionale
    assert pagina("https://www.sviluppo.toscana.it/minibond") == pagina(
        "https://www.sviluppo.toscana.it/bando/minibond/"
    )
    assert pagina("https://www.lazioinnova.it/bandi/basket-bond-lazio/") != pagina(
        "https://www.lazioinnova.it/bandi/basket-bond-lazio-contributo-sui-costi/"
    )
    assert pagina("https://www.regione.it/") is None and pagina(None) is None
