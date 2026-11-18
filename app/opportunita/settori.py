"""Settori di attività di Incentivi Italia (campo Settore_Attivita) a partire dall'ATECO.

Il dataset nazionale indica per molti bandi i settori ammessi con 21 voci fisse (verificate
sui dati del 18/11/2026). Dall'ATECO del cliente (divisione, prime due cifre) si ricavano i
settori possibili, in modo largo: una divisione manifatturiera vale anche «Artigianato»
perché un'impresa artigiana può esserlo. Si esclude un bando solo se nessuno dei settori
possibili è tra quelli ammessi; le divisioni non mappate non escludono mai.
"""

SETTORI = (
    "Agricoltura, silvicoltura e pesca", "Agroalimentare", "Alberghiero", "Altri servizi",
    "Artigianato", "Autoveicoli e altri mezzi di trasporto", "Chimica e Farmaceutica",
    "Commercio", "Cultura", "Edilizia", "Elettronica",
    "Fornitura Energia, Acqua e gestione Rifiuti", "ICT", "Meccanica", "Metallurgia",
    "Mobili, Legno e Carta", "Moda e Tessile", "Ristorazione", "Salute",
    "Servizi di trasporto", "Turismo",
)  # fmt: skip

_A = "Artigianato"
_PER_DIVISIONE: dict[tuple[int, int], tuple[str, ...]] = {
    (1, 3): ("Agricoltura, silvicoltura e pesca", "Agroalimentare"),
    (10, 12): ("Agroalimentare", _A),
    (13, 15): ("Moda e Tessile", _A),
    (16, 17): ("Mobili, Legno e Carta", _A),
    (18, 18): ("Mobili, Legno e Carta", "Cultura", _A),
    (19, 21): ("Chimica e Farmaceutica",),
    (22, 23): ("Chimica e Farmaceutica", "Edilizia", "Metallurgia", _A),
    (24, 24): ("Metallurgia",),
    (25, 25): ("Metallurgia", "Meccanica", _A),
    (26, 26): ("Elettronica", "ICT"),
    (27, 27): ("Elettronica", "Meccanica", _A),
    (28, 28): ("Meccanica", "Metallurgia"),
    (29, 30): ("Autoveicoli e altri mezzi di trasporto", "Meccanica"),
    (31, 31): ("Mobili, Legno e Carta", _A),
    (32, 32): ("Altri servizi", "Moda e Tessile", "Salute", _A),
    (33, 33): ("Meccanica", "Elettronica", _A),
    (35, 39): ("Fornitura Energia, Acqua e gestione Rifiuti",),
    (41, 43): ("Edilizia", _A),
    (45, 45): ("Commercio", "Autoveicoli e altri mezzi di trasporto", _A),
    (46, 47): ("Commercio",),
    (49, 53): ("Servizi di trasporto",),
    (55, 55): ("Alberghiero", "Turismo"),
    (56, 56): ("Ristorazione", "Turismo"),
    (58, 58): ("Cultura", "ICT"),
    (59, 60): ("Cultura", "ICT"),
    (61, 63): ("ICT",),
    (64, 66): ("Altri servizi",),
    (68, 71): ("Altri servizi",),
    (72, 72): ("Altri servizi", "ICT", "Salute", "Chimica e Farmaceutica"),
    (73, 78): ("Altri servizi",),
    (79, 79): ("Turismo", "Altri servizi"),
    (80, 82): ("Altri servizi",),
    (85, 85): ("Altri servizi", "Cultura"),
    (86, 87): ("Salute",),
    (88, 88): ("Salute", "Altri servizi"),
    (90, 92): ("Cultura",),
    (93, 93): ("Altri servizi", "Cultura", "Turismo"),
    (94, 94): ("Altri servizi",),
    (95, 96): ("Altri servizi", _A),
}


def settori_possibili(codice_ateco: str | None) -> set[str] | None:
    """Settori di Incentivi Italia compatibili con l'ATECO (None se la divisione non è
    mappata: in quel caso il settore non esclude)."""
    if not codice_ateco:
        return None
    cifre = codice_ateco.replace(".", "").strip()[:2]
    if not cifre.isdigit():
        return None
    divisione = int(cifre)
    for (da, a), settori in _PER_DIVISIONE.items():
        if da <= divisione <= a:
            return set(settori)
    return None
