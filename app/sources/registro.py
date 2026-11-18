"""Elenco delle fonti di bandi attive nel codice.

Le fonti regionali si aggiungono qui, una per modulo in app/sources/regioni/, dopo averne
verificato formato, robots.txt e condizioni d'uso (vedi docs/fonti/).
"""

from app.sources.base import Fonte
from app.sources.incentivi_italia import FONTE as INCENTIVI_ITALIA
from app.sources.regioni.lazio import FONTE as REGIONE_LAZIO
from app.sources.regioni.toscana import FONTE as REGIONE_TOSCANA

FONTI: dict[str, Fonte] = {f.codice: f for f in (INCENTIVI_ITALIA, REGIONE_LAZIO, REGIONE_TOSCANA)}
