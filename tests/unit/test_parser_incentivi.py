"""Parser di Incentivi Italia, su casi costruiti e sul campione reale di 50 record."""

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from app.sources.incentivi_italia import FONTE
from app.sources.incentivi_italia.parser import (
    interpreta_aree_speciali,
    interpreta_ateco,
    interpreta_record,
    interpreta_territorio,
    requisiti_da_fonte,
)
from app.utils.territorio import REGIONI

CAMPIONE = Path(__file__).parents[1] / "fixtures" / "incentivi_italia_campione.json"


@pytest.mark.parametrize(
    ("testo", "atteso"),
    [
        (None, (None, None, False)),
        ("", (None, None, False)),
        ("Tutti i settori economici ammissibili a ricevere aiuti", (None, None, False)),
        ("ATECO 2025: 58.13; 58.12", (["58.13", "58.12"], "2025", False)),
        ("ATECO 2007: 01; 01.1; 10.11", (["01", "01.1", "10.11"], "2007", False)),
        ("82.30; 47.19;", (["82.30", "47.19"], None, True)),  # versione non dichiarata
        ("ATECO 2025: 58.13; 58.13", (["58.13"], "2025", False)),  # duplicati rimossi
        ("ATECO 2007: 10.11; ATECO 2025: 10.11", (["10.11"], None, True)),  # versioni miste
        ("settore manifatturiero", (None, None, True)),  # testo libero
    ],
)
def test_interpreta_ateco(testo, atteso):
    assert interpreta_ateco(testo) == atteso


def test_territorio_nazionale_regionale_comunale():
    assert interpreta_territorio(list(REGIONI), [" "]) == ("nazionale", None, None)
    assert interpreta_territorio(["Lazio"], [" "]) == ("regionale", ["Lazio"], None)
    assert interpreta_territorio(["Lombardia"], ["Alagna; Dorno"]) == (
        "comunale",
        ["Lombardia"],
        ["Alagna", "Dorno"],
    )
    assert interpreta_territorio([" "], None) == ("sconosciuto", None, None)
    # voci che non sono regioni (es. "Estero") vengono ignorate
    assert interpreta_territorio(["Estero"], None) == ("sconosciuto", None, None)


def test_aree_speciali():
    assert interpreta_aree_speciali(["Aree interne,ZES"]) == ["Aree interne", "ZES"]
    assert interpreta_aree_speciali(["Non applicabile"]) is None
    assert interpreta_aree_speciali(None) is None


def test_importi_zero_e_assurdi():
    req = requisiti_da_fonte(
        {
            "Regioni": ["Lazio"],
            "Spesa_Ammessa_Min": 0,
            "Agevolazione_Concedibile_Max": 50000.0,
            "Stanziamento_incentivo": 9.99e15,
        }
    )
    assert req.spesa_min is None  # 0 = non indicato
    assert req.importo_max == Decimal("50000.00")
    assert req.dotazione is None  # importo implausibile scartato…
    assert "dotazione" in req.da_verificare  # …e segnalato


def test_ente_locale_con_tutte_le_regioni_non_e_nazionale():
    req = requisiti_da_fonte({"Regioni": list(REGIONI), "Soggetto_Concedente": "COMUNE DI X"})
    assert req.territorio == "sconosciuto"
    assert "territorio" in req.da_verificare
    req = requisiti_da_fonte({"Regioni": list(REGIONI), "Soggetto_Concedente": "Invitalia"})
    assert req.territorio == "nazionale"


def test_dimensioni_mappate_e_non_classificabile_ignorato():
    req = requisiti_da_fonte(
        {
            "Dimensione_beneficiari": [
                "Media Impresa",
                "Microimpresa",
                "Non classificabile/classificato",
            ]
        }
    )
    assert req.dimensioni == ["micro", "media"]
    req = requisiti_da_fonte({"Dimensione_beneficiari": ["Non classificabile/classificato"]})
    assert req.dimensioni is None
    assert "dimensioni" in req.da_verificare


def test_campi_mai_dalla_fonte_sono_da_verificare():
    req = requisiti_da_fonte({})
    assert {"regime", "modalita", "categorie_spesa"} <= set(req.da_verificare)


def test_record_senza_id_o_titolo_scartato():
    assert interpreta_record({"Titolo": "x"}) is None
    assert interpreta_record({"ID_Incentivo": "1", "Titolo": " "}) is None


def test_campione_reale_completo():
    records = FONTE.interpreta(CAMPIONE)
    assert len(records) == 50
    assert len({r.id_esterno for r in records}) == 50
    for r in records:
        assert r.titolo and r.id_esterno
        for campo in dataclasses.fields(r.requisiti):
            valore = getattr(r.requisiti, campo.name)
            # Regola fondamentale: mai liste vuote (None = nessun vincolo noto)
            assert valore != [], f"{r.id_esterno}: {campo.name} è una lista vuota"
    territori = {r.requisiti.territorio for r in records}
    assert {"nazionale", "regionale", "comunale"} <= territori
    assert any(r.requisiti.ateco_versione == "2025" for r in records)
    assert any(r.data_chiusura is None for r in records)


def test_json_non_valido(tmp_path):
    from app.sources.incentivi_italia import ErroreFormato

    p = tmp_path / "rotto.json"
    p.write_text('[{"ID_Incentivo": "1"', encoding="utf-8")  # troncato
    with pytest.raises(ErroreFormato):
        FONTE.interpreta(p)


def test_date_dalle_note_quando_mancano_i_campi():
    """19 bandi con lo stato «sconosciuto» il 20/11/2026: le date erano solo nelle note."""
    from datetime import date

    from app.sources.incentivi_italia.parser import date_dalle_note

    assert date_dalle_note("dal 30.01.2026 al 03.03.2026") == (date(2026, 1, 30), date(2026, 3, 3))
    assert date_dalle_note("Entro il 31/12/2026.") == (None, date(2026, 12, 31))
    assert date_dalle_note("entro le ore 12.00 del 30/06/2026") == (None, date(2026, 6, 30))
    assert date_dalle_note("COMPETENZA ANNO 2026") == (None, None)
    assert date_dalle_note("2026/2026") == (None, None)
    assert date_dalle_note("entro il 31/02/2026") == (None, None)  # data impossibile
    base = {"ID_Incentivo": "x1", "Titolo": "Bando"}
    rec = interpreta_record({**base, "Note_di_apertura_chiusura": "Entro il 31/12/2026."})
    assert rec.data_chiusura == date(2026, 12, 31)
    # i campi della fonte, quando ci sono, prevalgono sulle note
    rec = interpreta_record({**base, "Data_chiusura": "2026-05-01",
                             "Note_di_apertura_chiusura": "Entro il 31/12/2026."})  # fmt: skip
    assert rec.data_chiusura == date(2026, 5, 1)
