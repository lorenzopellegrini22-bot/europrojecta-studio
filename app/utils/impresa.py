"""Calcoli sull'impresa: dimensione (Raccomandazione 2003/361/CE), anzianità e plafond
de minimis residuo. Funzioni pure.

ATTENZIONE, sono stime:
- la dimensione vera richiede le ULA e i dati delle imprese associate o collegate: per questo
  la dimensione indicata a mano prevale sempre;
- il de minimis si calcola per «impresa unica», che può comprendere imprese collegate.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.config import get_settings

MILIONE = Decimal("1000000")


def dimensione_calcolata(
    dipendenti: int | None, fatturato: Decimal | None, totale_attivo: Decimal | None
) -> str | None:
    """micro / piccola / media / grande, oppure None se mancano gli occupati o entrambi gli
    importi. Soglie: occupati E (fatturato OPPURE totale di bilancio)."""
    if dipendenti is None or (fatturato is None and totale_attivo is None):
        return None

    def entro(soglia_fatturato: Decimal, soglia_attivo: Decimal) -> bool:
        return (fatturato is not None and fatturato <= soglia_fatturato) or (
            totale_attivo is not None and totale_attivo <= soglia_attivo
        )

    if dipendenti < 10 and entro(2 * MILIONE, 2 * MILIONE):
        return "micro"
    if dipendenti < 50 and entro(10 * MILIONE, 10 * MILIONE):
        return "piccola"
    if dipendenti < 250 and entro(50 * MILIONE, 43 * MILIONE):
        return "media"
    return "grande"


def dimensione_effettiva(azienda) -> tuple[str | None, str]:  # noqa: ANN001
    """(dimensione, provenienza): quella manuale prevale su quella calcolata."""
    if azienda.dimensione_manuale:
        return azienda.dimensione_manuale, "manuale"
    calcolata = dimensione_calcolata(azienda.dipendenti, azienda.fatturato, azienda.totale_attivo)
    return calcolata, "calcolata" if calcolata else "non nota"


def anzianita_mesi(data_costituzione: date | None, oggi: date | None = None) -> int | None:
    if data_costituzione is None:
        return None
    oggi = oggi or date.today()
    mesi = (oggi.year - data_costituzione.year) * 12 + oggi.month - data_costituzione.month
    if oggi.day < data_costituzione.day:
        mesi -= 1
    return max(mesi, 0)


def massimali_de_minimis() -> dict[str, Decimal]:
    s = get_settings()
    return {
        "generale": Decimal(str(s.de_minimis_generale)),
        "agricolo": Decimal(str(s.de_minimis_agricolo)),
        "pesca": Decimal(str(s.de_minimis_pesca)),
        "sieg": Decimal(str(s.de_minimis_sieg)),
    }


@dataclass
class Plafond:
    regolamento: str
    massimale: Decimal
    utilizzato: Decimal
    residuo: Decimal
    manuale: bool = False


def _tre_anni_prima(oggi: date) -> date:
    try:
        return oggi.replace(year=oggi.year - 3)
    except ValueError:  # 29 febbraio
        return oggi.replace(year=oggi.year - 3, day=28)


def plafond_residuo(
    aiuti: Iterable,  # oggetti con data_concessione, importo, regolamento
    regolamento: str = "generale",
    manuale: Decimal | None = None,
    oggi: date | None = None,
) -> Plafond:
    """Plafond residuo sugli ultimi 3 anni mobili (dalla stessa data di 3 anni fa a oggi).
    Il valore manuale, se presente, prevale (vale per il regolamento generale)."""
    oggi = oggi or date.today()
    massimale = massimali_de_minimis()[regolamento]
    if manuale is not None and regolamento == "generale":
        return Plafond(regolamento, massimale, massimale - manuale, manuale, manuale=True)
    inizio = _tre_anni_prima(oggi)
    utilizzato = sum(
        (
            Decimal(a.importo)
            for a in aiuti
            if a.regolamento == regolamento and inizio < a.data_concessione <= oggi
        ),
        Decimal("0"),
    )
    return Plafond(regolamento, massimale, utilizzato, max(massimale - utilizzato, Decimal("0")))
