"""Controlli sull'output dell'AI, prima che tocchi i requisiti.

- La citazione deve comparire davvero nel testo del bando (confronto normalizzato: minuscole,
  senza accenti, spazi e punteggiatura compattati). Se non compare: confidenza "bassa" e
  campo "da verificare".
- Ogni valore deve rispettare il vocabolario e il tipo del campo; un valore fuori vocabolario
  viene scartato (null, da verificare).
- La sintesi non ha bisogno di citazione.
"""

import re
from datetime import date
from typing import Any

from app.extraction.schema import CAMPI
from app.utils.ricerca import normalizza

_NON_PAROLA = re.compile(r"[^a-z0-9]+")
_ATECO = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")


def _norm(testo: str) -> str:
    return _NON_PAROLA.sub(" ", normalizza(testo)).strip()


def citazione_presente(citazione: str | None, testo: str) -> bool:
    if not citazione:
        return False
    c = _norm(citazione)
    if len(c) < 3:
        return False
    return c in _norm(testo)


def _valore_valido(campo: Any, valore: Any) -> Any:
    """Il valore ripulito, oppure None se non rispetta tipo o vocabolario."""
    if valore is None:
        return None
    if campo.tipo == "lista":
        if not isinstance(valore, list):
            return None
        voci = [str(v).strip() for v in valore if str(v).strip()]
        if campo.valori:
            voci = [v for v in voci if v in campo.valori]
        if campo.nome.startswith("ateco"):
            voci = [v for v in voci if _ATECO.match(v)]
        voci = list(dict.fromkeys(voci))
        return voci or None
    if campo.tipo == "scelta":
        return valore if valore in campo.valori else None
    if campo.tipo in ("numero", "intero"):
        if isinstance(valore, bool) or not isinstance(valore, int | float):
            return None
        if valore < 0 or valore > 1e12:
            return None
        if campo.nome == "percentuale" and valore > 100:
            return None
        return int(valore) if campo.tipo == "intero" else valore
    if campo.tipo == "data":
        try:
            d = date.fromisoformat(str(valore)[:10])
        except ValueError:
            return None
        return d.isoformat() if 2000 <= d.year <= 2100 else None
    s = str(valore).strip()
    return s[:2000] or None


def verifica(output: dict[str, Any], testo: str) -> dict[str, dict[str, Any]]:
    """Output dell'AI → campi verificati {valore, confidenza, citazione, da_verificare}."""
    verificati: dict[str, dict[str, Any]] = {}
    for campo in CAMPI:
        grezzo = output.get(campo.nome) if isinstance(output, dict) else None
        if not isinstance(grezzo, dict):
            grezzo = {}
        valore = _valore_valido(campo, grezzo.get("valore"))
        confidenza = grezzo.get("confidenza")
        if confidenza not in ("alta", "media", "bassa"):
            confidenza = "bassa"
        citazione = (grezzo.get("citazione") or None) and str(grezzo["citazione"])[:600]
        da_verificare = bool(grezzo.get("da_verificare")) or valore is None
        if grezzo.get("valore") is not None and valore is None:
            da_verificare = True  # valore fuori vocabolario o non valido: scartato
            confidenza = "bassa"
        if (
            valore is not None
            and campo.nome != "sintesi"
            and not citazione_presente(citazione, testo)
        ):
            confidenza = "bassa"
            da_verificare = True
        if confidenza == "bassa":
            da_verificare = True
        verificati[campo.nome] = {
            "valore": valore,
            "confidenza": confidenza,
            "citazione": citazione,
            "da_verificare": da_verificare,
        }
    return verificati
