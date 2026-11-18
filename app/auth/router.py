"""Pagine di accesso: login, secondo fattore, configurazione della 2FA, logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import servizio, totp
from app.auth.dipendenze import (
    sessione_opzionale,
    sessione_parziale,
    utente_corrente,
    verifica_csrf,
)
from app.auth.password import hash_password, valida_nuova_password
from app.auth.sessioni import (
    cancella_cookie,
    crea_sessione,
    elimina_sessione,
    imposta_cookie,
    promuovi_sessione,
)
from app.config import get_settings
from app.db import get_db
from app.models import Sessione, Utente
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


def _apri_sessione(request: Request, db: Session, utente: Utente) -> Response:
    """Dopo la password giusta: sessione parziale verso la 2FA, oppure completa se
    l'installazione non usa la verifica in due passaggi."""
    ip, ua = _client(request)
    sessione, token = crea_sessione(db, utente, request)
    if not get_settings().verifica_due_passaggi:
        servizio.completa_accesso(db, utente, ip, ua)
        token = promuovi_sessione(sessione)
        destinazione = "/"
    else:
        destinazione = "/login/2fa" if utente.totp_attivo else "/2fa/configura"
    db.commit()
    risposta = _vai(destinazione)
    imposta_cookie(risposta, token)
    return risposta


def nessun_utente(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(Utente)) == 0


def _primo_accesso_aperto(db: Session) -> bool:
    """La pagina «Primo accesso» esiste solo con PRIMO_ACCESSO=true e finché l'app non ha
    nessun utente: serve a creare il primo titolare dove non si può usare la riga di comando
    (es. Vercel). Appena c'è un utente sparisce da sola."""
    return get_settings().primo_accesso and nessun_utente(db)


@router.get("/login")
def login_pagina(
    request: Request,
    db: Db,
    sessione: Annotated[Sessione | None, Depends(sessione_opzionale)],
) -> Response:
    if sessione is not None and sessione.mfa_verificata:
        return _vai("/")
    if _primo_accesso_aperto(db):
        return _vai("/primo-accesso")
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
    return _apri_sessione(request, db, esito.utente)


@router.get("/primo-accesso")
def primo_accesso_pagina(request: Request, db: Db) -> Response:
    if not _primo_accesso_aperto(db):
        raise HTTPException(status_code=404)
    return render(request, "auth/primo_accesso.html")


@router.post("/primo-accesso")
def primo_accesso_invio(
    request: Request,
    db: Db,
    email: Annotated[str, Form()],
    nome: Annotated[str, Form()],
    password: Annotated[str, Form()],
    ripeti: Annotated[str, Form()],
) -> Response:
    # Un solo primo titolare anche con due invii contemporanei
    db.execute(text("SELECT pg_advisory_xact_lock(7311001)"))
    if not _primo_accesso_aperto(db):
        raise HTTPException(status_code=404)
    email = email.strip().lower()
    nome = nome.strip()
    errore = None
    if "@" not in email or not nome:
        errore = "Scrivi email e nome."
    elif errore_password := valida_nuova_password(password):
        errore = errore_password
    elif password != ripeti:
        errore = "Le due password non coincidono."
    if errore:
        return render(
            request,
            "auth/primo_accesso.html",
            {"errore": errore, "email": email, "nome": nome},
            status_code=400,
        )
    utente = Utente(email=email, nome=nome, password_hash=hash_password(password), ruolo="titolare")
    db.add(utente)
    db.flush()
    return _apri_sessione(request, db, utente)


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
