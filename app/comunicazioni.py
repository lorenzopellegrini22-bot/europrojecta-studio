"""Invio di email registrato: ogni tentativo finisce in notifica_email, riuscito o no.

È l'unico punto da cui il server spedisce email (comunicazioni delle pratiche, promemoria,
prove). Se l'integrazione SMTP è disattivata o non configurata non parte nulla.
"""

from sqlalchemy.orm import Session

from app.integrazioni import servizio as integrazioni
from app.integrazioni.posta import ErroreInvio, invia
from app.models import NotificaEmail, Utente
from app.sicurezza.cifratura import ErroreCifratura


def email_disponibile(db: Session) -> bool:
    return integrazioni.utilizzabile(db, "smtp")


def invia_registrando(
    db: Session,
    tipo: str,
    destinatario: str,
    oggetto: str,
    testo: str,
    utente: Utente | None = None,
    pratica_id: int | None = None,
    rispondi_a: str | None = None,
    anche_se_disattivata: bool = False,
) -> tuple[bool, str]:
    """Restituisce (riuscito, messaggio per l'utente). Fa commit della riga di registro."""
    stato = integrazioni.stato(db, "smtp")
    if not stato.configurata:
        return False, "L'email non è configurata: collegala in Impostazioni → Integrazioni."
    if not stato.attiva and not anche_se_disattivata:
        return False, "L'integrazione email è disattivata in Impostazioni → Integrazioni."
    try:
        invia(integrazioni.leggi_valori(db, "smtp"), destinatario, oggetto, testo, rispondi_a)
        esito, errore, messaggio = "ok", None, f"Email inviata a {destinatario}."
    except ErroreInvio as e:
        esito, errore, messaggio = "errore", str(e), str(e)
    except ErroreCifratura:
        esito, errore = "errore", "Password SMTP non decifrabile"
        messaggio = "La password SMTP salvata non si può decifrare: reinseriscila."
    db.add(
        NotificaEmail(
            tipo=tipo,
            destinatario=destinatario,
            oggetto=oggetto,
            corpo=testo,
            pratica_id=pratica_id,
            utente_id=utente.id if utente else None,
            esito=esito,
            errore=errore,
        )
    )
    db.commit()
    return esito == "ok", messaggio
