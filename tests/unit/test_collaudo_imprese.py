"""Casi trovati nel collaudo con le 10 aziende di prova (24/11/2026)."""

from types import SimpleNamespace as N

from app.collaudo import AZIENDE_PROVA, azienda_di_prova
from app.opportunita.abbinamento import PUNTEGGIO_MINIMO, Contesto, valuta_bando

AZ = {d["chiave"]: d for d in AZIENDE_PROVA}


def _bando(titolo, descrizione="", regioni=None):
    req = N(
        tipologie_soggetto=["Impresa"],
        territorio="regionale" if regioni else "nazionale",
        regioni=regioni,
        comuni=None,
        dimensioni=None,
        ateco_ammessi=None,
        ateco_esclusi=None,
        regime=None,
        forme_giuridiche=None,
        categorie_spesa=None,
        finalita=None,
        anzianita_max_mesi=None,
        anzianita_min_mesi=None,
        regime_aiuto=None,
        importo_min=None,
        importo_max=None,
    )
    return N(
        titolo=titolo,
        descrizione=descrizione,
        stato="aperto",
        ente=None,
        raw={},
        id_esterno="x",
        requisiti=req,
        data_apertura=None,
        data_chiusura=None,
    )


def _punteggio(chiave, b):
    a, progetti = azienda_di_prova(AZ[chiave])
    es = valuta_bando(a, b, Contesto(progetti=progetti))
    return es.punteggio if es else None


def test_requisiti_rari_restano_sotto_la_soglia():
    wbo = _bando(
        "Fondo rotativo regionale per il recupero di aziende in crisi o a rischio di "
        "delocalizzazione",
        "Workers buyout di cooperative di ex dipendenti.",
        ["Lazio"],
    )
    assert _punteggio("A06", wbo) < PUNTEGGIO_MINIMO
    alluvione = _bando(
        "Indennizzo dei danni materiali diretti subiti dalle imprese esportatrici",
        "Imprese colpite dagli eventi alluvionali del 2023.",
        ["Toscana"],
    )
    assert _punteggio("A04", alluvione) < PUNTEGGIO_MINIMO
    confidi = _bando(
        "Fondo di Riassicurazione",
        "Invito ai Confidi a presentare la propria manifestazione d'interesse.",
        ["Umbria"],
    )
    assert _punteggio("A02", confidi) < PUNTEGGIO_MINIMO


def test_bandi_nazionali_e_progetti():
    termico = _bando(
        "Conto Termico 3.0",
        "Incentiva interventi di efficienza energetica e "
        "produzione di energia termica da fonti rinnovabili negli edifici.",
    )
    assert _punteggio("A04", termico) >= PUNTEGGIO_MINIMO  # riqualificazione della sede
    assert _punteggio("A10", termico) < PUNTEGGIO_MINIMO  # flotta di camion: non sono edifici
    assert _punteggio("A03", termico) < PUNTEGGIO_MINIMO  # software house: nessun progetto
