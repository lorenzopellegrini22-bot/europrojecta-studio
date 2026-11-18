"""Invio di email tramite il server SMTP configurato in Impostazioni → Integrazioni.

Ogni invio (riuscito o no) va registrato in notifica_email da chi chiama: qui c'è solo il
trasporto. Testo semplice, UTF-8, mittente e nome mittente dalle impostazioni.
"""

import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

TIMEOUT_SECONDI = 20


class ErroreInvio(Exception):
    """Messaggio già pensato per il consulente."""


def componi(
    valori: dict[str, str | None],
    destinatario: str,
    oggetto: str,
    testo: str,
    rispondi_a: str | None = None,
) -> EmailMessage:
    mittente = valori.get("mittente")
    if not mittente:
        raise ErroreInvio("Manca l'indirizzo mittente nelle impostazioni email.")
    msg = EmailMessage()
    nome = valori.get("nome_mittente")
    msg["From"] = formataddr((nome, mittente)) if nome else mittente
    msg["To"] = destinatario
    msg["Subject"] = oggetto
    msg["Message-ID"] = make_msgid(domain=Address(addr_spec=mittente).domain or None)
    if rispondi_a:
        msg["Reply-To"] = rispondi_a
    msg.set_content(testo)
    return msg


def invia(
    valori: dict[str, str | None],
    destinatario: str,
    oggetto: str,
    testo: str,
    rispondi_a: str | None = None,
) -> None:
    host, utente, password = valori.get("host"), valori.get("utente"), valori.get("password")
    if not host or not utente or not password:
        raise ErroreInvio("L'email non è configurata: mancano server, utente o password.")
    if "@" not in (destinatario or ""):
        raise ErroreInvio("Indirizzo del destinatario non valido.")
    msg = componi(valori, destinatario, oggetto, testo, rispondi_a)
    try:
        porta = int(valori.get("porta") or 587)
    except ValueError as e:
        raise ErroreInvio("La porta SMTP deve essere un numero.") from e
    contesto = ssl.create_default_context()
    try:
        if valori.get("sicurezza") == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host, porta, timeout=TIMEOUT_SECONDI, context=contesto
            )
        else:
            server = smtplib.SMTP(host, porta, timeout=TIMEOUT_SECONDI)
            server.starttls(context=contesto)
        try:
            server.login(utente, password)
            server.send_message(msg)
        finally:
            server.quit()
    except smtplib.SMTPAuthenticationError as e:
        raise ErroreInvio("Utente o password SMTP non corretti.") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise ErroreInvio(f"Il server ha rifiutato il destinatario {destinatario}.") from e
    except smtplib.SMTPException as e:
        raise ErroreInvio(f"Il server SMTP ha risposto con un errore: {e}") from e
    except (TimeoutError, ssl.SSLError, OSError) as e:
        raise ErroreInvio(f"Impossibile collegarsi al server SMTP {host}:{porta}.") from e
