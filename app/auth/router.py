"""Pagine di accesso: login, secondo fattore, configurazione della 2FA, logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth import servizio, totp
from app.auth.dipendenze import (
    sessione_opzionale,
    sessione_parziale,
    utente_corrente,
    verifica_csrf,
)
from app.auth.sessioni import (
    cancella_cookie,
    crea_sessione,
    elimina_sessione,
    imposta_cookie,
    promuovi_sessione,
)
from app.db import get_db
from app.models import Sessione
from app.web import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _vai(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/sessione/rinnova", dependencies=[Depends(utente_corrente)])
def rinnova_sessione() -> Response:
    """Chiamata dal pulsante «Resta collegato»: basta essere autenticati per aggiornare
    l'ultimo uso della sessione (vedi app/auth/sessioni.py)."""
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/login")
def login_pagina(
    request: Request, sessione: Annotated[Sessione | None, Depends(sessione_opzionale)]
) -> Response:
    if sessione is not None and sessione.mfa_verificata:
        return _vai("/")
    return render(request, "auth/login.html")


@router.post("/login")
def login_invio(
    request: Request,
    db: Db,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    ip, ua = _client(request)
    esito = servizio.verifica_credenziali(db, email, password, ip, ua)
    if not esito.ok or esito.utente is None:
        return render(
            request,
            "auth/login.html",
            {"errore": esito.messaggio, "email": email},
            status_code=400,
        )
    utente = esito.utente
    _, token = crea_sessione(db, utente, request)
    db.commit()
    risposta = _vai("/login/2fa" if utente.totp_attivo else "/2fa/configura")
    imposta_cookie(risposta, token)
    return risposta


@router.get("/login/2fa")
def secondo_fattore_pagina(
    request: Request, sessione: Annotated[Sessione, Depends(sessione_parziale)]
) -> Response:
    if sessione.mfa_verificata:
        return _vai("/")
    if not sessione.utente.totp_attivo:
        return _vai("/2fa/configura")
    return render(request, "auth/secondo_fattore.html")


@router.post("/login/2fa", dependencies=[Depends(verifica_csrf)])
def secondo_fattore_invio(
    request: Request,
    db: Db,
    sessione: Annotated[Sessione, Depends(sessione_parziale)],
    codice: Annotated[str, Form()],
) -> Response:
    ip, ua = _client(request)
    utente = sessione.utente
    esito = servizio.verifica_secondo_fattore(db, utente, codice, ip, ua)
    if not esito.ok:
        if servizio.is_bloccato(utente):
            elimina_sessione(db, sessione)
            risposta = render(request, "auth/login.html", {"errore": esito.messaggio}, 400)
            cancella_cookie(risposta)
            return risposta
        return render(request, "auth/secondo_fattore.html", {"errore": esito.messaggio}, 400)
    servizio.completa_accesso(db, utente, ip, ua)
    token = promuovi_sessione(sessione)
    db.commit()
    risposta = _vai("/")
    imposta_cookie(risposta, token)
    return risposta


@router.get("/2fa/configura")
def configura_2fa_pagina(
    request: Request, db: Db, sessione: Annotated[Sessione, Depends(sessione_parziale)]
) -> Response:
    utente = sessione.utente
    if utente.totp_attivo:
        return _vai("/" if sessione.mfa_verificata else "/login/2fa")
    segreto = servizio.prepara_totp(utente)
    db.commit()
    uri = totp.uri_provisioning(segreto, utente.email)
    return render(
        request,
        "auth/configura_2fa.html",
        {"qr_svg": totp.qr_svg(uri), "segreto": segreto},
    )


@router.post("/2fa/configura", dependencies=[Depends(verifica_csrf)])
def configura_2fa_invio(
    request: Request,
    db: Db,
    sessione: Annotated[Sessione, Depends(sessione_parziale)],
    codice: Annotated[str, Form()],
) -> Response:
    utente = sessione.utente
    if utente.totp_attivo:
        return _vai("/")
    codici = servizio.attiva_totp(db, utente, codice)
    if codici is None:
        segreto = servizio.segreto_totp(utente)
        if segreto is None:
            return _vai("/2fa/configura")
        return render(
            request,
            "auth/configura_2fa.html",
            {
                "qr_svg": totp.qr_svg(totp.uri_provisioning(segreto, utente.email)),
                "segreto": segreto,
                "errore": "Codice non valido: controlla l'ora del telefono e riprova.",
            },
            status_code=400,
        )
    ip, ua = _client(request)
    servizio.completa_accesso(db, utente, ip, ua)
    token = promuovi_sessione(sessione)
    db.commit()
    risposta = render(request, "auth/codici_recupero.html", {"codici": codici})
    imposta_cookie(risposta, token)
    return risposta


@router.post("/logout", dependencies=[Depends(verifica_csrf)])
def logout(db: Db, sessione: Annotated[Sessione, Depends(sessione_parziale)]) -> Response:
    elimina_sessione(db, sessione)
    risposta = _vai("/login")
    cancella_cookie(risposta)
    return risposta
