"""Confronto dei codici ATECO tra versioni diverse (2007 aggiornamento 2022 ↔ 2025) con la
tavola di raccordo ISTAT.

Esito del confronto di un codice con una lista del bando:
- "si":    il codice (o tutte le sue conversioni) è nella lista;
- "no":    né il codice né alcuna conversione è nella lista;
- "forse": la conversione non è univoca (alcune corrispondenze sì, altre no) oppure non si
           conosce la versione del bando e solo una conversione corrisponde.
Il matching esclude solo con "no": nei casi incerti il requisito è «da verificare».
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AtecoRaccordo


def cifre(codice: str) -> str:
    """Solo le cifre (o la lettera della sezione): «62.01.00» → «620100»."""
    return codice.replace(".", "").replace(" ", "").upper()


def nella_lista(codice: str, lista: list[str]) -> bool:
    """Confronto per prefisso: «01» nella lista copre tutta l'agricoltura."""
    c = cifre(codice)
    return any(c.startswith(cifre(x)) for x in lista if x)


@dataclass
class Raccordo:
    da_2007: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    da_2025: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def converti(self, codice: str, da_versione: str) -> set[str]:
        tabella = self.da_2007 if da_versione == "2007" else self.da_2025
        c = cifre(codice)
        # Se il codice è più dettagliato di quelli in tabella, si risale di livello
        while c and c not in tabella:
            c = c[:-1]
        return set(tabella.get(c, set()))


def confronta(
    codice: str,
    versione_azienda: str | None,
    lista: list[str],
    versione_bando: str | None,
    raccordo: Raccordo | None,
) -> str:
    diretto = nella_lista(codice, lista)
    if raccordo is None or not versione_azienda:
        return "si" if diretto else "no"
    if versione_bando == versione_azienda:
        return "si" if diretto else "no"
    convertiti = raccordo.converti(codice, versione_azienda)
    esiti = {nella_lista(c, lista) for c in convertiti}
    if versione_bando:  # versioni diverse e note: conta solo la conversione
        if not esiti:
            return "forse"
        return "si" if esiti == {True} else ("no" if esiti == {False} else "forse")
    # versione del bando non nota: vale il confronto diretto, la conversione solo se aiuta
    if diretto:
        return "si"
    return "forse" if True in esiti else "no"


_CACHE: dict[str, Raccordo | None] = {}


def carica(db: Session) -> Raccordo | None:
    r = Raccordo()
    for riga in db.scalars(select(AtecoRaccordo)):
        a, b = cifre(riga.codice_2025), cifre(riga.codice_2007)
        r.da_2025[a].add(b)
        r.da_2007[b].add(a)
    return r if r.da_2007 else None


def in_memoria(db: Session) -> Raccordo | None:
    if _CACHE.get("r") is None:
        _CACHE["r"] = carica(db)
    return _CACHE["r"]


def azzera_cache() -> None:
    _CACHE.clear()
