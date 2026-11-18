"""Elenco delle fonti di bandi attive nel codice.

Le fonti regionali si aggiungono qui, una per modulo in app/sources/regioni/, dopo averne
verificato formato, robots.txt e condizioni d'uso (vedi docs/fonti/).
"""

from app.sources.base import Fonte
from app.sources.incentivi_italia import FONTE as INCENTIVI_ITALIA

FONTI: dict[str, Fonte] = {INCENTIVI_ITALIA.codice: INCENTIVI_ITALIA}
