from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.utils.impresa import anzianita_mesi, dimensione_calcolata, plafond_residuo

M = Decimal("1000000")


def test_dimensione_secondo_la_raccomandazione():
    assert dimensione_calcolata(5, Decimal("1500000"), None) == "micro"
    assert dimensione_calcolata(9, 3 * M, Decimal("1900000")) == "micro"  # basta il bilancio
    assert dimensione_calcolata(10, Decimal("1000000"), None) == "piccola"  # 10 occupati
    assert dimensione_calcolata(49, 9 * M, None) == "piccola"
    assert dimensione_calcolata(120, 60 * M, 40 * M) == "media"  # bilancio entro 43 M€
    assert dimensione_calcolata(120, 60 * M, 45 * M) == "grande"
    assert dimensione_calcolata(250, 1 * M, None) == "grande"
    assert dimensione_calcolata(None, M, None) is None
    assert dimensione_calcolata(5, None, None) is None


def test_anzianita_in_mesi():
    assert anzianita_mesi(date(2024, 3, 15), date(2026, 3, 14)) == 23
    assert anzianita_mesi(date(2024, 3, 15), date(2026, 3, 15)) == 24
    assert anzianita_mesi(None) is None


def _aiuto(giorno, importo, regolamento="generale"):
    return SimpleNamespace(
        data_concessione=giorno, importo=Decimal(importo), regolamento=regolamento
    )


def test_plafond_de_minimis_tre_anni_mobili():
    oggi = date(2026, 11, 18)
    aiuti = [
        _aiuto(date(2023, 11, 18), "100000"),  # esattamente 3 anni fa: fuori
        _aiuto(date(2024, 1, 10), "50000"),
        _aiuto(date(2026, 6, 1), "20000"),
        _aiuto(date(2025, 1, 1), "30000", "agricolo"),
    ]
    p = plafond_residuo(aiuti, "generale", oggi=oggi)
    assert p.utilizzato == Decimal("70000") and p.residuo == Decimal("230000")
    agricolo = plafond_residuo(aiuti, "agricolo", oggi=oggi)
    assert agricolo.residuo == Decimal("20000")
    manuale = plafond_residuo(aiuti, "generale", manuale=Decimal("12345"), oggi=oggi)
    assert manuale.manuale and manuale.residuo == Decimal("12345")
