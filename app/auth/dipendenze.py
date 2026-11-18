"""Dipendenze FastAPI per autenticazione, ruoli e CSRF.

Uso nelle route:
    utente: Annotated[Utente, Depends(utente_corrente)]          # login + 2FA
    utente: Annotated[Utente, Depends(richiede_titolare)]        # solo il titolare
    dependencies=[Depends(verifica_csrf)]                        # su ogni POST
"""

import hmac
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.sessioni import COOKIE, carica_sessione
from app.db import get_db
from app.models import Sessione, Utente


class NonAutenticato(Exception):
    """Serve il login: l'app reindirizza a /login."""


class Proibito(Exception):
    """Utente autenticato ma senza i permessi necessari."""


class CsrfNonValido(Exception):
    """Token CSRF mancante o errato."""


def sessione_opzionale(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> Sessione | None:
    """La sessione corrente (anche parziale), o None. Memorizzata in request.state."""
    if not hasattr(request.state, "sessione"):
        request.state.sessione = carica_sessione(db, request.cookies.get(COOKIE))
    return request.state.sessione


def sessione_parziale(
    sessione: Annotated[Sessione | None, Depends(sessione_opzionale)],
) -> Sessione:
    """Sessione con password verificata ma 2FA ancora da completare."""
    if sessione is None:
        raise NonAutenticato
    return sessione


def utente_corrente(
    sessione: Annotated[Sessione | None, Depends(sessione_opzionale)],
) -> Utente:
    """Utente con login completo (password + 2FA)."""
    if sessione is None or not sessione.mfa_verificata:
        raise NonAutenticato
    return sessione.utente


def controllo_accesso(
    request: Request,
    sessione: Annotated[Sessione | None, Depends(sessione_opzionale)],
) -> None:
    """Su ogni richiesta: l'utente vede solo le sezioni assegnate e, se in sola lettura,
    non modifica nulla (app/auth/permessi.py). Le pagine senza login non sono toccate."""
    if sessione is None or not sessione.mfa_verificata:
        return
    from app.auth.permessi import puo_modificare, puo_vedere

    percorso = request.url.path
    if not puo_vedere(sessione.utente, percorso) or not puo_modificare(
        sessione.utente, percorso, request.method
    ):
        raise Proibito


def richiede_titolare(utente: Annotated[Utente, Depends(utente_corrente)]) -> Utente:
    if not utente.is_titolare:
        raise Proibito
    return utente


async def verifica_csrf(
    request: Request,
    sessione: Annotated[Sessione | None, Depends(sessione_opzionale)],
) -> None:
    """Il token CSRF della sessione deve arrivare nell'header X-CSRF-Token (HTMX)
    oppure nel campo csrf_token del form."""
    if sessione is None:
        raise NonAutenticato
    inviato = request.headers.get("x-csrf-token")
    if not inviato:
        form = await request.form()
        valore = form.get("csrf_token")
        inviato = valore if isinstance(valore, str) else None
    if not inviato or not hmac.compare_digest(inviato, sessione.csrf_token):
        raise CsrfNonValido
