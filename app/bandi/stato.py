"""Stato calcolato di un bando a partire dalle date (funzione pura)."""

from datetime import date


def calcola_stato(
    apertura: date | None,
    chiusura: date | None,
    oggi: date,
    scomparso: bool = False,
) -> str:
    """
    - chiuso: data di chiusura passata, oppure bando scomparso dalla fonte;
    - in_arrivo: apertura futura;
    - aperto: apertura passata (o assente) e chiusura futura o assente
      (es. "fino a esaurimento risorse");
    - sconosciuto: nessuna data.
    Il giorno di chiusura è ancora "aperto".
    """
    if scomparso:
        return "chiuso"
    if chiusura is not None and chiusura < oggi:
        return "chiuso"
    if apertura is not None and apertura > oggi:
        return "in_arrivo"
    if apertura is None and chiusura is None:
        return "sconosciuto"
    return "aperto"
