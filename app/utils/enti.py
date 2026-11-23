"""Enti pubblici come clienti: riconoscimento dal nome e limiti di popolazione dei bandi."""

import re

from app.utils.ricerca import normalizza

# Inizio della ragione sociale o forma giuridica di un ente pubblico
_ENTI = re.compile(
    r"^(comune|provincia|citta metropolitana|regione|unione (dei |di )?comuni|"
    r"comunita montana|consorzio di bonifica|azienda sanitaria|asl|asp|"
    r"ente pubblico|ente locale)\b"
)


def tipo_dal_nome(ragione_sociale: str | None, forma_giuridica: str | None = None) -> str:
    """«impresa» o «ente_pubblico» dal nome: serve come valore iniziale (nel modulo si può
    sempre cambiare). «Comune di Bassano in Teverina» → ente_pubblico."""
    for testo in (forma_giuridica, ragione_sociale):
        if testo and _ENTI.match(normalizza(testo).strip()):
            return "ente_pubblico"
    return "impresa"


# «piccoli comuni»: legge 158/2017, fino a 5.000 abitanti
PICCOLI_COMUNI = 5000
_PICCOLI = re.compile(r"\bpiccoli comuni\b")
_LIMITE = re.compile(
    r"(?:fino a|inferiore a|inferiori a|sotto i|non superiore a|non superiori a|meno di|"
    r"al di sotto dei|minore di|pari o inferiore a)\s+(\d{1,3}(?:[.\s]\d{3})*|\d+)\s+abitanti"
)


def limite_abitanti(testo: str | None) -> int | None:
    """Popolazione massima chiesta dal bando, se il testo la dice; None altrimenti."""
    if not testo:
        return None
    t = normalizza(testo)
    m = _LIMITE.search(t)
    if m:
        return int(re.sub(r"\D", "", m.group(1)))
    if _PICCOLI.search(t):
        return PICCOLI_COMUNI
    return None
