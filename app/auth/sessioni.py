"""Sessioni lato server.

Il browser riceve solo un token casuale nel cookie (HttpOnly, SameSite=Lax, Secure in
produzione). Nel database si salva l'impronta HMAC del token, non il token: chi legge il
database non può impersonare un utente.

Ogni sessione ha due scadenze: per inattività e assoluta. Prima della verifica 2FA la
sessione è "parziale" e scade dopo pochi minuti.
"""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Sessione, Utente
from app.sicurezza.cifratura import impronta

COOKIE = "europrojecta_sessione"
SCOPO_TOKEN = "sessione"
DURATA_SESSIONE_PARZIALE = timedelta(minutes=10)
# Aggiorna ultimo_uso al massimo una volta al minuto, per non scrivere a ogni richiesta.
AGGIORNA_ULTIMO_USO_OGNI = timedelta(minutes=1)


def adesso() -> datetime:
    return datetime.now(UTC)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def crea_sessione(db: Session, utente: Utente, request: Request) -> tuple[Sessione, str]:
    """Nuova sessione parziale (2FA non ancora verificata). Restituisce sessione e token."""
    token = secrets.token_urlsafe(32)
    ora = adesso()
    sessione = Sessione(
        token_hash=impronta(token, SCOPO_TOKEN),
        csrf_token=secrets.token_urlsafe(32),
        utente_id=utente.id,
        mfa_verificata=False,
        scade_il=ora + DURATA_SESSIONE_PARZIALE,
        ultimo_uso=ora,
        ip=_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:500],
    )
    db.add(sessione)
    return sessione, token


def promuovi_sessione(sessione: Sessione) -> str:
    """Dopo la 2FA: sessione completa, nuovo token (contro la session fixation),
    nuovo token CSRF e scadenza assoluta piena. Restituisce il nuovo token."""
    token = secrets.token_urlsafe(32)
    ora = adesso()
    sessione.token_hash = impronta(token, SCOPO_TOKEN)
    sessione.csrf_token = secrets.token_urlsafe(32)
    sessione.mfa_verificata = True
    sessione.scade_il = ora + timedelta(hours=get_settings().sessione_durata_max_ore)
    sessione.ultimo_uso = ora
    return token


def carica_sessione(db: Session, token: str | None) -> Sessione | None:
    """La sessione valida legata al token, o None. Le sessioni scadute vengono eliminate."""
    if not token:
        return None
    sessione = db.scalar(
        select(Sessione).where(Sessione.token_hash == impronta(token, SCOPO_TOKEN))
    )
    if sessione is None:
        return None
    ora = adesso()
    inattivita = timedelta(minutes=get_settings().sessione_inattivita_minuti)
    if ora >= sessione.scade_il or ora - sessione.ultimo_uso >= inattivita:
        db.delete(sessione)
        db.commit()
        return None
    if not sessione.utente.attivo:
        db.delete(sessione)
        db.commit()
        return None
    if ora - sessione.ultimo_uso >= AGGIORNA_ULTIMO_USO_OGNI:
        sessione.ultimo_uso = ora
        db.commit()
    return sessione


def elimina_sessione(db: Session, sessione: Sessione) -> None:
    db.delete(sessione)
    db.commit()


def elimina_sessioni_utente(db: Session, utente_id: int) -> None:
    db.execute(delete(Sessione).where(Sessione.utente_id == utente_id))


def imposta_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.sessione_durata_max_ore * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def cancella_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
