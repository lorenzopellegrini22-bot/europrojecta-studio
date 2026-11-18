"""Ruoli e sezioni: chi vede e chi modifica cosa.

Ruoli:
- titolare: tutto, comprese Impostazioni e gestione degli utenti;
- collaboratore: lavora nelle sezioni che il titolare gli assegna;
- lettura: vede le sezioni assegnate ma non modifica nulla (es. un consulente esterno).

Sezioni assegnabili (Impostazioni → Utenti). Se a un collaboratore non sono state scelte
sezioni valgono quelle predefinite del brief: tutto tranne importazione ed esportazione dei dati.
Impostazioni e utenti restano sempre del solo titolare.

Il controllo avviene su ogni richiesta (dipendenza globale in app/main.py) in base
all'indirizzo: aggiungere una pagina sotto un prefisso esistente la protegge già.
"""

from app.models import Utente

RUOLI = {
    "titolare": "Titolare (tutto, comprese impostazioni e utenti)",
    "collaboratore": "Collaboratore (lavora nelle sezioni scelte)",
    "lettura": "Sola lettura (vede le sezioni scelte, non modifica)",
}

# chiave → (etichetta, prefissi degli indirizzi)
SEZIONI: dict[str, tuple[str, tuple[str, ...]]] = {
    "clienti": ("Clienti, referenti, progetti e attività", ("/clienti",)),
    "opportunita": ("Opportunità e feedback", ("/opportunita",)),
    "pratiche": ("Pratiche, scadenzario e modelli", ("/pratiche", "/scadenzario", "/modelli")),
    "bandi": ("Bandi, fonti, revisione AI e PDF", ("/bandi", "/fonti", "/revisione", "/documenti")),
    "appalti": ("Appalti pubblici", ("/appalti",)),
    "aggiornamenti": ("Aggiornamenti ai clienti e report PDF", ("/aggiornamenti", "/report")),
    "prefattibilita": ("Prefattibilità", ("/prefattibilita",)),
    "segnalatori": ("Segnalatori", ("/segnalatori",)),
    "statistiche": ("Statistiche", ("/statistiche",)),
    "importazione": ("Importazione dei clienti da CSV", ("/import",)),
    "esportazione": ("Esportazione dei dati", ("/export", "/pratiche/export.csv")),
}
PREDEFINITE_COLLABORATORE = tuple(k for k in SEZIONI if k not in ("importazione", "esportazione"))
SOLO_TITOLARE = ("/impostazioni",)
# Indirizzi che anche chi è in sola lettura può chiamare in POST (il proprio account)
POST_SEMPRE_AMMESSI = ("/logout", "/account", "/sessione", "/2fa")


def _corrisponde(percorso: str, prefisso: str) -> bool:
    return (
        percorso == prefisso
        or percorso.startswith(prefisso.rstrip("/") + "/")
        or ("." in prefisso and percorso.startswith(prefisso))
    )


def sezione_di(percorso: str) -> str | None:
    """Sezione di un indirizzo (il prefisso più lungo vince: /pratiche/export.csv è
    «esportazione», non «pratiche»)."""
    migliore: tuple[int, str] | None = None
    for chiave, (_e, prefissi) in SEZIONI.items():
        for p in prefissi:
            if _corrisponde(percorso, p) and (migliore is None or len(p) > migliore[0]):
                migliore = (len(p), chiave)
    return migliore[1] if migliore else None


def sezioni_di(utente: Utente) -> set[str]:
    if utente.ruolo == "titolare":
        return set(SEZIONI)
    scelte = getattr(utente, "sezioni", None)
    if scelte is None:
        return set(PREDEFINITE_COLLABORATORE)
    return {s for s in scelte if s in SEZIONI}


def puo_vedere(utente: Utente, percorso: str) -> bool:
    if any(_corrisponde(percorso, p) for p in SOLO_TITOLARE):
        return utente.ruolo == "titolare"
    sezione = sezione_di(percorso)
    return sezione is None or sezione in sezioni_di(utente)


def puo_modificare(utente: Utente, percorso: str, metodo: str) -> bool:
    if metodo in ("GET", "HEAD", "OPTIONS") or utente.ruolo != "lettura":
        return True
    return any(_corrisponde(percorso, p) for p in POST_SEMPRE_AMMESSI)
