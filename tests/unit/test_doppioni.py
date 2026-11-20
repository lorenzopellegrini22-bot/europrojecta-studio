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


def test_invitalia_collegata_alle_misure_nazionali():
    """Titoli brevi di Invitalia contro quelli lunghi di Incentivi Italia (dati del 20/11/2026)."""
    from app.bandi.doppioni import parole

    breve, lungo = (
        parole("Smart&Start Italia"),
        parole("Smart&Start Italia - Sostegno alle startup innovative"),
    )
    assert breve <= lungo and len(breve) >= 2
    # «Legge 181» non basta per unirla a una singola area di crisi
    assert (
        somiglianza("Legge 181", "Legge 181/1989 - Riqualificazione dell'area di crisi di Gela")
        < 0.3
    )
