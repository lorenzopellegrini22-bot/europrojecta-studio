"""Stato calcolato di un bando a partire dalle date (funzione pura)."""

from datetime import date

STATI_DICHIARABILI = ("aperto", "in_arrivo", "chiuso")


def calcola_stato(
    apertura: date | None,
    chiusura: date | None,
    oggi: date,
    scomparso: bool = False,
    stato_fonte: str | None = None,
) -> str:
    """
    - chiuso: data di chiusura passata, bando scomparso dalla fonte, oppure la fonte stessa
      lo dichiara chiuso (es. risorse esaurite prima della scadenza);
    - in_arrivo: apertura futura;
    - aperto: apertura passata (o assente) e chiusura futura o assente
      (es. "fino a esaurimento risorse");
    - senza date: vale lo stato dichiarato dalla fonte (portali regionali), altrimenti
      sconosciuto.
    Le date, quando ci sono, prevalgono sullo stato dichiarato, tranne "chiuso".
    Il giorno di chiusura è ancora "aperto".
    """
    if scomparso or stato_fonte == "chiuso":
        return "chiuso"
    if chiusura is not None and chiusura < oggi:
        return "chiuso"
    if apertura is not None and apertura > oggi:
        return "in_arrivo"
    if apertura is None and chiusura is None:
        return stato_fonte if stato_fonte in STATI_DICHIARABILI else "sconosciuto"
    return "aperto"
