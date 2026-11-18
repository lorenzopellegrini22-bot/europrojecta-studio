"""Pulsante "Verifica connessione": prova le credenziali senza effetti collaterali.

- OpenAI: elenca i modelli disponibili (chiamata gratuita, nessun token consumato).
- SMTP: si collega, attiva TLS e fa il login, poi chiude. Nessuna email inviata.

I messaggi sono pensati per il consulente, non per uno sviluppatore.
"""

import smtplib
import socket
import ssl
from dataclasses import dataclass

import openai


@dataclass
class EsitoVerifica:
    ok: bool
    messaggio: str


TIMEOUT_SECONDI = 15


def verifica_openai(valori: dict[str, str | None]) -> EsitoVerifica:
    chiave = valori.get("api_key")
    if not chiave:
        return EsitoVerifica(False, "Manca l'API key.")
    client = openai.OpenAI(api_key=chiave, max_retries=0, timeout=TIMEOUT_SECONDI)
    try:
        modelli = client.models.list()
        quanti = sum(1 for _ in modelli)
    except openai.AuthenticationError:
        return EsitoVerifica(
            False,
            "Chiave non valida: OpenAI l'ha rifiutata. Controlla di averla copiata per intero.",
        )
    except openai.PermissionDeniedError:
        return EsitoVerifica(
            False, "La chiave non ha i permessi necessari (o il progetto OpenAI è limitato)."
        )
    except openai.RateLimitError as e:
        if e.code == "insufficient_quota":
            return EsitoVerifica(
                False,
                "Credito OpenAI esaurito o limite di spesa raggiunto: "
                "controlla la fatturazione nel tuo account OpenAI.",
            )
        return EsitoVerifica(
            False, "OpenAI ha limitato temporaneamente le richieste: riprova tra qualche minuto."
        )
    except (openai.APIConnectionError, openai.APITimeoutError):
        return EsitoVerifica(False, "Impossibile raggiungere OpenAI: problema di rete dal server.")
    except openai.APIStatusError as e:
        return EsitoVerifica(False, f"OpenAI ha risposto con un errore ({e.status_code}).")
    finally:
        client.close()
    return EsitoVerifica(
        True, f"Connessione riuscita: la chiave è valida ({quanti} modelli disponibili)."
    )


def verifica_smtp(valori: dict[str, str | None]) -> EsitoVerifica:
    host, utente, password = valori.get("host"), valori.get("utente"), valori.get("password")
    if not host or not utente or not password:
        return EsitoVerifica(False, "Mancano server, utente o password.")
    try:
        porta = int(valori.get("porta") or 587)
    except ValueError:
        return EsitoVerifica(False, "La porta deve essere un numero.")
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
        finally:
            server.quit()
    except smtplib.SMTPAuthenticationError:
        return EsitoVerifica(False, "Utente o password SMTP non corretti.")
    except smtplib.SMTPNotSupportedError:
        return EsitoVerifica(
            False, "Il server non supporta STARTTLS su questa porta: prova SSL sulla 465."
        )
    except ssl.SSLError:
        return EsitoVerifica(
            False, "Errore TLS: controlla porta e tipo di sicurezza (587 STARTTLS, 465 SSL)."
        )
    except (TimeoutError, socket.gaierror, ConnectionError, OSError):
        return EsitoVerifica(
            False, f"Impossibile collegarsi a {host}:{porta}. Controlla server e porta."
        )
    except smtplib.SMTPException as e:
        return EsitoVerifica(False, f"Il server SMTP ha risposto con un errore: {e}")
    return EsitoVerifica(True, "Connessione e login riusciti. Nessuna email è stata inviata.")


VERIFICHE = {"openai": verifica_openai, "smtp": verifica_smtp}
