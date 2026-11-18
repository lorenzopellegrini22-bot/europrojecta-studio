"""Logica del login: credenziali, blocco dopo i tentativi falliti, 2FA, codici di recupero.

Tutti i tentativi, riusciti o no, finiscono in log_accesso.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import totp
from app.auth.password import (
    hash_password,
    serve_rehash,
    valida_nuova_password,
    verifica_password,
)
from app.config import get_settings
from app.models import CodiceRecupero, LogAccesso, Utente
from app.sicurezza.cifratura import cifra, decifra

SCOPO_TOTP = "totp"
MESSAGGIO_CREDENZIALI = "Email o password non corretti."
MESSAGGIO_IP_BLOCCATO = "Troppi tentativi da questo indirizzo: riprova tra qualche minuto."


@dataclass
class EsitoLogin:
    ok: bool
    utente: Utente | None = None
    messaggio: str = ""


def _adesso() -> datetime:
    return datetime.now(UTC)


def _log(
    db: Session,
    email: str,
    utente: Utente | None,
    esito: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    db.add(
        LogAccesso(
            email_tentata=email[:254],
            utente_id=utente.id if utente else None,
            esito=esito,
            ip=ip,
            user_agent=(user_agent or "")[:500],
        )
    )


def _messaggio_blocco(utente: Utente) -> str:
    fuso = ZoneInfo(get_settings().fuso_orario)
    ora_locale = utente.bloccato_fino_al.astimezone(fuso).strftime("%H:%M")  # type: ignore[union-attr]
    return f"Troppi tentativi falliti: accesso bloccato fino alle {ora_locale}."


def is_bloccato(utente: Utente) -> bool:
    return utente.bloccato_fino_al is not None and utente.bloccato_fino_al > _adesso()


def _fallimento(db: Session, utente: Utente) -> None:
    """Conta un tentativo fallito (password o 2FA) e blocca al raggiungimento del limite."""
    settings = get_settings()
    utente.tentativi_falliti += 1
    if utente.tentativi_falliti >= settings.login_max_tentativi:
        utente.bloccato_fino_al = _adesso() + timedelta(minutes=settings.login_blocco_minuti)
        utente.tentativi_falliti = 0


def troppi_tentativi_da_ip(db: Session, ip: str) -> bool:
    """Limite per indirizzo IP, oltre a quello per account: chi prova molte email diverse
    dallo stesso indirizzo viene fermato per la durata del blocco."""
    settings = get_settings()
    da = _adesso() - timedelta(minutes=settings.login_blocco_minuti)
    falliti = db.scalar(
        select(func.count())
        .select_from(LogAccesso)
        .where(LogAccesso.ip == ip, LogAccesso.esito != "ok", LogAccesso.creato_il >= da)
    )
    return (falliti or 0) >= settings.login_max_tentativi_ip


def cambia_password(
    db: Session, utente: Utente, attuale: str, nuova: str, conferma: str
) -> str | None:
    """Restituisce un messaggio d'errore, o None se la password è stata cambiata."""
    if not verifica_password(utente.password_hash, attuale):
        return "La password attuale non è corretta."
    if nuova != conferma:
        return "Le due nuove password non coincidono."
    if nuova == attuale:
        return "La nuova password deve essere diversa da quella attuale."
    errore = valida_nuova_password(nuova)
    if errore:
        return errore
    utente.password_hash = hash_password(nuova)
    return None


def rigenera_codici_recupero(utente: Utente) -> list[str]:
    codici = totp.genera_codici_recupero()
    utente.codici_recupero.clear()
    for c in codici:
        utente.codici_recupero.append(CodiceRecupero(codice_hash=totp.hash_codice_recupero(c)))
    return codici


def verifica_solo_totp(utente: Utente, codice: str) -> bool:
    """Conferma di un'operazione sensibile con il codice dell'app (non i codici di recupero)."""
    segreto = segreto_totp(utente)
    if not segreto or not utente.totp_attivo:
        return False
    intervallo = totp.verifica_codice(segreto, codice, utente.totp_ultimo_intervallo)
    if intervallo is None:
        return False
    utente.totp_ultimo_intervallo = intervallo
    return True


def verifica_credenziali(
    db: Session, email: str, password: str, ip: str | None, user_agent: str | None
) -> EsitoLogin:
    email = email.strip().lower()
    utente = db.scalar(select(Utente).where(Utente.email == email))

    if ip and troppi_tentativi_da_ip(db, ip):
        _log(db, email, utente, "bloccato", ip, user_agent)
        db.commit()
        return EsitoLogin(False, messaggio=MESSAGGIO_IP_BLOCCATO)

    if utente is None:
        verifica_password(None, password)  # stesso tempo di risposta di un utente esistente
        _log(db, email, None, "utente_inesistente", ip, user_agent)
        db.commit()
        return EsitoLogin(False, messaggio=MESSAGGIO_CREDENZIALI)

    if is_bloccato(utente):
        _log(db, email, utente, "bloccato", ip, user_agent)
        db.commit()
        return EsitoLogin(False, messaggio=_messaggio_blocco(utente))

    if not utente.attivo or not verifica_password(utente.password_hash, password):
        _fallimento(db, utente)
        _log(db, email, utente, "password_errata", ip, user_agent)
        db.commit()
        if is_bloccato(utente):
            return EsitoLogin(False, messaggio=_messaggio_blocco(utente))
        return EsitoLogin(False, messaggio=MESSAGGIO_CREDENZIALI)

    if serve_rehash(utente.password_hash):
        utente.password_hash = hash_password(password)
    db.commit()
    return EsitoLogin(True, utente=utente)


def verifica_secondo_fattore(
    db: Session, utente: Utente, codice: str, ip: str | None, user_agent: str | None
) -> EsitoLogin:
    """Accetta un codice TOTP oppure un codice di recupero non ancora usato."""
    if is_bloccato(utente):
        _log(db, utente.email, utente, "bloccato", ip, user_agent)
        db.commit()
        return EsitoLogin(False, messaggio=_messaggio_blocco(utente))

    if utente.totp_attivo and utente.totp_secret_cifrato:
        segreto = decifra(utente.totp_secret_cifrato, SCOPO_TOTP)
        intervallo = totp.verifica_codice(segreto, codice, utente.totp_ultimo_intervallo)
        if intervallo is not None:
            utente.totp_ultimo_intervallo = intervallo
            return EsitoLogin(True, utente=utente)

        codice_hash = totp.hash_codice_recupero(codice)
        recupero = db.scalar(
            select(CodiceRecupero).where(
                CodiceRecupero.utente_id == utente.id,
                CodiceRecupero.codice_hash == codice_hash,
                CodiceRecupero.usato_il.is_(None),
            )
        )
        if recupero is not None:
            recupero.usato_il = _adesso()
            return EsitoLogin(True, utente=utente)

    _fallimento(db, utente)
    _log(db, utente.email, utente, "totp_errato", ip, user_agent)
    db.commit()
    if is_bloccato(utente):
        return EsitoLogin(False, messaggio=_messaggio_blocco(utente))
    return EsitoLogin(False, messaggio="Codice non valido.")


def completa_accesso(db: Session, utente: Utente, ip: str | None, user_agent: str | None) -> None:
    utente.tentativi_falliti = 0
    utente.bloccato_fino_al = None
    utente.ultimo_accesso = _adesso()
    _log(db, utente.email, utente, "ok", ip, user_agent)


def prepara_totp(utente: Utente) -> str:
    """Genera e salva (cifrato) un nuovo segreto TOTP non ancora attivo."""
    segreto = totp.nuovo_segreto()
    utente.totp_secret_cifrato = cifra(segreto, SCOPO_TOTP)
    utente.totp_attivo = False
    return segreto


def segreto_totp(utente: Utente) -> str | None:
    if not utente.totp_secret_cifrato:
        return None
    return decifra(utente.totp_secret_cifrato, SCOPO_TOTP)


def attiva_totp(db: Session, utente: Utente, codice: str) -> list[str] | None:
    """Attiva la 2FA se il codice è corretto e crea 10 nuovi codici di recupero
    (restituiti in chiaro una sola volta). None se il codice è sbagliato."""
    segreto = segreto_totp(utente)
    if segreto is None:
        return None
    intervallo = totp.verifica_codice(segreto, codice, None)
    if intervallo is None:
        return None
    utente.totp_attivo = True
    utente.totp_ultimo_intervallo = intervallo
    utente.codici_recupero.clear()
    codici = totp.genera_codici_recupero()
    for c in codici:
        utente.codici_recupero.append(CodiceRecupero(codice_hash=totp.hash_codice_recupero(c)))
    return codici
