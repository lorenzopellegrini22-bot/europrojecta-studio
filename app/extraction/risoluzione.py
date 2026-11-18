"""Risoluzione dei requisiti di un bando: correzione manuale > dato della fonte > AI.

Funzione pura: riceve i tre livelli e restituisce i valori delle colonne di bando_requisiti,
la provenienza di ogni campo e l'elenco dei campi da verificare.

Dettagli:
- un dato della fonte che la fonte stessa segna "da verificare" (es. territorio di un ente
  locale che dichiara tutte le regioni) cede il passo a un valore dell'AI con confidenza alta
  o media; altrimenti resta, sempre da verificare;
- un valore dell'AI con confidenza bassa si usa solo se non c'è altro, ed è da verificare;
- "territorio" non è mai null: se nessuno lo sa è "sconosciuto".
"""

from decimal import Decimal
from typing import Any

from app.extraction.schema import CAMPI

# Colonne di bando_requisiti che la risoluzione gestisce (quelle con un campo AI o di fonte)
COLONNE = tuple(
    dict.fromkeys(
        [c.colonna for c in CAMPI if c.colonna]
        + [
            "aree_speciali",
            "ateco_versione",
            "tipologie_soggetto",
            "settori",
            "finalita",
            "spesa_min",
            "spesa_max",
        ]
    )
)
NUMERICHE = {"percentuale", "importo_min", "importo_max", "dotazione", "spesa_min", "spesa_max"}


def _presente(campo: str, valore: Any) -> bool:
    if valore is None or valore == []:
        return False
    return not (campo == "territorio" and valore == "sconosciuto")


def _converti(colonna: str, valore: Any) -> Any:
    if valore is None:
        return None
    if colonna in NUMERICHE:
        return Decimal(str(valore))
    if isinstance(valore, list):
        return list(valore) or None
    return valore


def risolvi(
    fonte: dict[str, Any] | None,
    fonte_da_verificare: list[str] | None,
    ai: dict[str, dict[str, Any]] | None,
    manuale: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    fonte = fonte or {}
    ai = ai or {}
    manuale = manuale or {}
    incerti_fonte = set(fonte_da_verificare or [])
    # Nomi generici usati dalle fonti → colonne
    alias = {"ateco": ("ateco_ammessi",), "importi": ("importo_min", "importo_max", "dotazione")}
    for generico, colonne in alias.items():
        if generico in incerti_fonte:
            incerti_fonte.update(colonne)
    valori: dict[str, Any] = {}
    provenienza: dict[str, str] = {}
    da_verificare: list[str] = []
    for col in COLONNE:
        v_ai = ai.get(col) or {}
        ai_valore = v_ai.get("valore")
        ai_buono = _presente(col, ai_valore) and v_ai.get("confidenza") in ("alta", "media")
        ai_incerto = bool(v_ai.get("da_verificare"))
        if col in manuale:
            scelto, origine, incerto = manuale[col], "manuale", False
        elif _presente(col, fonte.get(col)) and col not in incerti_fonte:
            scelto, origine, incerto = fonte[col], "fonte", False
        elif ai_buono:
            scelto, origine, incerto = ai_valore, "llm", ai_incerto
        elif _presente(col, fonte.get(col)):
            scelto, origine, incerto = fonte[col], "fonte", True
        elif _presente(col, ai_valore):
            scelto, origine, incerto = ai_valore, "llm", True
        else:
            scelto, origine = None, None
            incerto = col in incerti_fonte or (bool(v_ai) and ai_incerto)
        if col == "territorio" and scelto is None:
            scelto = "sconosciuto"
            incerto = True
        valori[col] = _converti(col, scelto)
        if origine:
            provenienza[col] = origine
        if incerto:
            da_verificare.append(col)
    # Campi non di colonna che la fonte segnalava (es. "date") restano da verificare
    for extra in sorted(incerti_fonte - set(COLONNE) - {"ateco", "importi"}):
        if extra not in manuale and extra not in da_verificare:
            da_verificare.append(extra)
    return valori, provenienza, da_verificare
