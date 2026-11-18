# ruff: noqa: E501
"""Tabelle ISTAT: lettura dei file (stesse intestazioni di quelli ufficiali), caricamento,
riconoscimento del comune della sede, comuni e province dei bandi, raccordo ATECO."""

import io
from types import SimpleNamespace as N

import openpyxl
import pytest

from app.opportunita.abbinamento import Contesto, valuta_bando_con_motivo
from app.riferimenti import ateco, istat, territorio

pytestmark = pytest.mark.integration

INTESTAZIONI_COMUNI = [
    "Codice Regione", "Codice dell'Unità territoriale sovracomunale (valida a fini statistici)",
    "Codice Provincia (Storico)(1)", "Progressivo del Comune (2)",
    "Codice Comune formato alfanumerico", "Denominazione (Italiana e straniera)",
    "Denominazione in italiano", "Denominazione altra lingua", "Codice Ripartizione Geografica",
    "Ripartizione geografica", "Denominazione Regione",
    "Denominazione dell'Unità territoriale sovracomunale (valida a fini statistici)",
    "Tipologia di Unità territoriale sovracomunale", "Flag Comune capoluogo di Provincia",
    "Sigla automobilistica", "Codice Comune formato numerico", "Codice Catastale del Comune",
]  # fmt: skip
COMUNI = [
    ("056059", "Viterbo", "Lazio", "Viterbo", "VT", "M082"),
    ("058091", "Roma", "Lazio", "Roma", "RM", "H501"),
    ("048017", "Firenze", "Toscana", "Firenze", "FI", "D612"),
    ("027033", "San Donà di Piave", "Veneto", "Venezia", "VE", "H823"),
    ("015146", "Milano", "Lombardia", "Milano", "MI", "F205"),
]


def _xlsx_comuni(n_copie: int = 1500) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CODICI al 21_02_2026"
    ws.append(INTESTAZIONI_COMUNI)
    # il caricatore rifiuta file con meno di 7.000 comuni: si replicano righe fittizie
    righe = list(COMUNI) + [
        (f"9{i:05d}", f"Comune prova {i}", "Molise", "Campobasso", "CB", None)
        for i in range(n_copie * 5)
    ]
    for codice, nome, regione, prov, sigla, catasto in righe:
        r = [""] * len(INTESTAZIONI_COMUNI)
        r[4], r[6], r[10], r[11], r[14], r[16] = codice, nome, regione, prov, sigla, catasto
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _xlsx_raccordo() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ATECO 2025 vs ATECO 2022"
    ws.append(["ORDINE_CODICE_ATECO_2025", "CODICE_ATECO_2025", "TITOLO_ITALIANO_ATECO_2025",
               "COPERTURA_ATECO_2025", "ORDINE_CODICE_ATECO_2022", "CODICE_ATECO_2022",
               "TITOLO_ITALIANO_ATECO_2022", "COPERTURA_ATECO_2022"])  # fmt: skip
    righe = [("62.10", "62.01"), ("62.10.00", "62.01.00"), ("47.11", "47.91"), ("47.91", "47.91")]
    righe += [(f"99.{i:04d}", f"99.{i:04d}") for i in range(5000)]
    for a, b in righe:
        ws.append([1, a, "", "TOTALE", 1, b, "", "PARZIALE"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def istat_caricato(db):
    comuni = istat.leggi_comuni(_xlsx_comuni())
    raccordo = istat.leggi_raccordo(_xlsx_raccordo())
    istat.carica(db, comuni, raccordo)
    yield
    istat.carica(db, [], [])  # svuota: gli altri test lavorano senza tabelle ISTAT


def test_lettura_e_riconoscimento(db, istat_caricato):
    t = territorio.in_memoria(db)
    assert t.comune("san dona' di piave").codice == "027033"
    assert t.comune("SAN DONA DI PIAVE").sigla == "VE"
    assert t.sigla("Città metropolitana di Roma Capitale") == "RM"
    assert t.sigla("Provincia di Firenze") == "FI" and t.sigla("Atlantide") is None
    sede = N(comune_testo="Viterbo", provincia=None, regione=None, codice_istat=None)
    assert territorio.completa_sede(t, sede)
    assert (sede.codice_istat, sede.provincia, sede.regione) == ("056059", "VT", "Lazio")


def test_lettura_rifiuta_file_troppo_corti():
    with pytest.raises(istat.ErroreIstat):
        istat.leggi_comuni(_xlsx_comuni(n_copie=1))


def test_matching_con_codici_istat(db, istat_caricato):
    ctx = Contesto(territorio=territorio.in_memoria(db), raccordo=ateco.in_memoria(db), oggi=None)
    a = N(
        sede_legale=N(regione="Veneto", comune_testo="San Donà di Piave", provincia="VE", codice_istat=None),
        dimensione_manuale="piccola",
        ateco_primario=N(codice="62.01", versione="2007"),
    )  # fmt: skip

    def bando(**req):
        base = {"tipologie_soggetto": None, "territorio": "regionale", "regioni": ["Veneto"],
                "comuni": None, "province": None, "dimensioni": None, "ateco_ammessi": None,
                "ateco_versione": None}  # fmt: skip
        base.update(req)
        return N(titolo="Contributi alle imprese", stato="aperto", ente=None, requisiti=N(**base))

    # nome scritto diversamente (senza accento): prima era un'esclusione sbagliata
    es, motivo = valuta_bando_con_motivo(
        a, bando(territorio="comunale", comuni=["San Dona' di Piave"]), ctx
    )
    assert es is not None, motivo
    _, motivo = valuta_bando_con_motivo(a, bando(territorio="comunale", comuni=["Milano"]), ctx)
    assert motivo and "altri comuni" in motivo
    # province per esteso confrontate per sigla
    es, _ = valuta_bando_con_motivo(a, bando(territorio="provinciale", province=["Venezia"]), ctx)
    assert any("provincia della sede (VE)" in m for m in es.soddisfatti)
    _, motivo = valuta_bando_con_motivo(
        a, bando(territorio="provinciale", province=["Firenze"]), ctx
    )
    assert motivo and "altre province" in motivo
    # ATECO 2007 del cliente contro elenco ATECO 2025 del bando
    es, _ = valuta_bando_con_motivo(a, bando(ateco_ammessi=["62.10"], ateco_versione="2025"), ctx)
    assert any("tra gli ammessi" in m for m in es.soddisfatti)
    _, motivo = valuta_bando_con_motivo(
        a, bando(ateco_ammessi=["47.11"], ateco_versione="2025"), ctx
    )
    assert motivo and "non tra gli ammessi" in motivo
    # versione del bando sconosciuta: una corrispondenza solo dopo la conversione → da verificare
    es, _ = valuta_bando_con_motivo(a, bando(ateco_ammessi=["62.10"]), ctx)
    assert es is not None and any("raccordo ATECO" in m for m in es.da_verificare)
