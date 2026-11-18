"""Fase 3: schema strict, verifica delle citazioni, risoluzione manuale > fonte > AI."""

from decimal import Decimal

from app.extraction.risoluzione import risolvi
from app.extraction.schema import CAMPI, schema_json
from app.extraction.verifica import citazione_presente, verifica

TESTO = (
    "Titolo: Bando Digitalizzazione PMI\n"
    "Descrizione: Possono partecipare le micro, piccole e medie imprese con sede in Toscana. "
    "Il contributo a fondo perduto è pari al 50% delle spese ammissibili, fino a un massimo "
    "di euro 20.000. Gli aiuti sono concessi in regime «de minimis». Le domande si presentano "
    "a sportello dal 1° dicembre 2026 al 31 gennaio 2027."
)


def _campo(valore, citazione=None, confidenza="alta", da_verificare=False):
    return {
        "valore": valore,
        "confidenza": confidenza,
        "citazione": citazione,
        "da_verificare": da_verificare,
    }


def test_schema_strict():
    s = schema_json()
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {c.nome for c in CAMPI}
    for nome, p in s["properties"].items():
        assert p["additionalProperties"] is False, nome
        assert p["required"] == ["valore", "confidenza", "citazione", "da_verificare"]
    assert "null" in s["properties"]["regime"]["properties"]["valore"]["type"]
    assert None in s["properties"]["regime"]["properties"]["valore"]["enum"]


def test_citazione_normalizzata():
    assert citazione_presente("in regime «de minimis»", TESTO)
    assert citazione_presente("IN REGIME DE MINIMIS", TESTO)  # maiuscole e virgolette
    assert not citazione_presente("regime di esenzione GBER", TESTO)
    assert not citazione_presente("", TESTO)


def test_verifica():
    out = verifica(
        {
            "regime": _campo("de_minimis", "concessi in regime «de minimis»"),
            "percentuale": _campo(50, "pari al 50% delle spese ammissibili"),
            # citazione inventata: confidenza bassa e da verificare
            "importo_max": _campo(30000, "fino a un massimo di euro 30.000"),
            # valore fuori vocabolario: scartato
            "dimensioni": _campo(["PMI"], "micro, piccole e medie imprese"),
            "regioni": _campo(["Toscana"], "con sede in Toscana"),
            "modalita": _campo("sportello", "Le domande si presentano a sportello"),
            "sintesi": _campo("Contributo per la digitalizzazione delle PMI toscane."),
        },
        TESTO,
    )
    assert out["regime"]["valore"] == "de_minimis" and not out["regime"]["da_verificare"]
    assert out["percentuale"]["valore"] == 50 and out["percentuale"]["confidenza"] == "alta"
    assert out["importo_max"]["confidenza"] == "bassa" and out["importo_max"]["da_verificare"]
    assert out["dimensioni"]["valore"] is None and out["dimensioni"]["da_verificare"]
    assert out["sintesi"]["valore"] and not out["sintesi"]["da_verificare"]
    # campi non restituiti: null e da verificare
    assert out["dotazione"]["valore"] is None and out["dotazione"]["da_verificare"]


def test_risoluzione_precedenze():
    fonte = {"territorio": "regionale", "regioni": ["Toscana"], "importo_max": "25000"}
    ai = {
        "importo_max": _campo(20000, "x"),
        "regime": _campo("de_minimis", "x"),
        "modalita": _campo("sportello", "x", confidenza="bassa", da_verificare=True),
        "regioni": _campo(["Lazio"], "x"),
    }
    valori, prov, dv = risolvi(fonte, ["regime", "modalita"], ai, {"regime": "esenzione"})
    assert valori["importo_max"] == Decimal("25000") and prov["importo_max"] == "fonte"
    assert valori["regioni"] == ["Toscana"]  # la fonte prevale sull'AI
    assert valori["regime"] == "esenzione" and prov["regime"] == "manuale"
    assert "regime" not in dv
    assert valori["modalita"] == "sportello" and "modalita" in dv  # AI incerta
    assert valori["territorio"] == "regionale"


def test_risoluzione_fonte_incerta_e_vuota():
    # territorio della fonte incerto (ente locale "nazionale"): l'AI affidabile prevale
    valori, prov, dv = risolvi(
        {"territorio": "sconosciuto", "dimensioni": None},
        ["territorio", "dimensioni", "date", "importi"],
        {"territorio": _campo("comunale", "x"), "comuni": _campo(["Prato"], "x")},
        None,
    )
    assert valori["territorio"] == "comunale" and prov["territorio"] == "llm"
    assert valori["comuni"] == ["Prato"]
    assert "dimensioni" in dv and "date" in dv and "importo_max" in dv
    assert "importi" not in dv
    # nessun dato da nessuno: territorio sconosciuto, da verificare
    valori, _, dv = risolvi(None, None, None, None)
    assert valori["territorio"] == "sconosciuto" and "territorio" in dv


def test_valori_dai_requisiti_per_bandi_vecchi():
    from types import SimpleNamespace

    from app.extraction.applica import valori_dai_requisiti
    from app.extraction.risoluzione import COLONNE

    req = SimpleNamespace(
        **{c: None for c in COLONNE},
        provenienza={"regioni": "fonte", "dimensioni": "fonte"},
        da_verificare=["regime"],
    )
    req.territorio = "regionale"
    req.regioni = ["Lazio"]
    req.dimensioni = ["micro"]
    v = valori_dai_requisiti(req)
    assert v["regioni"] == ["Lazio"] and v["territorio"] == "regionale"
    assert v["_da_verificare"] == ["regime"] and "regime" not in v
