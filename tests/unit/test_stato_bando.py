from datetime import date

import pytest

from app.bandi.stato import calcola_stato

OGGI = date(2026, 11, 18)


@pytest.mark.parametrize(
    ("apertura", "chiusura", "atteso"),
    [
        (date(2026, 1, 1), date(2026, 12, 31), "aperto"),
        (date(2026, 1, 1), OGGI, "aperto"),  # il giorno di chiusura è ancora aperto
        (date(2026, 1, 1), date(2026, 11, 17), "chiuso"),
        (date(2026, 12, 1), date(2027, 1, 31), "in_arrivo"),
        (date(2026, 1, 1), None, "aperto"),  # fino a esaurimento risorse
        (None, date(2026, 12, 31), "aperto"),
        (None, date(2025, 1, 1), "chiuso"),
        (None, None, "sconosciuto"),
    ],
)
def test_calcola_stato(apertura, chiusura, atteso):
    assert calcola_stato(apertura, chiusura, OGGI) == atteso


def test_scomparso_dalla_fonte_e_chiuso():
    assert calcola_stato(date(2026, 1, 1), None, OGGI, scomparso=True) == "chiuso"
