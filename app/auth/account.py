"""Pagina "Il mio account": password, codici di recupero, sessioni attive, ultimi accessi."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth import servizio
from app.auth.dipendenze import sessione_parziale, utente_corrente, verifica_csrf
from app.db import get_db
from app.models import CodiceRecupero, LogAccesso, Utente
from app.models import Sessione as SessioneModel
from app.web import render

router = APIRouter(prefix="/account")

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
SessioneCorrente = Annotated[SessioneModel, Depends(sessione_parziale)]

MESSAGGI = {
    "password": "Password cambiata. Gli altri dispositivi sono stati disconnessi.",
    "sessioni": "Sei uscito da tutti gli altri dispositivi.",
}


def _pagina(
    request: Request,
    db: Session,
    utente: Utente,
    sessione: SessioneModel,
    status_code: int = 200,
    **extra: object,
) -> Response:
    sessioni = db.scalars(
        select(SessioneModel)
        .where(SessioneModel.utente_id == utente.id, SessioneModel.mfa_verificata.is_(True))
        .order_by(SessioneModel.ultimo_uso.desc())
    ).all()
    accessi = db.scalars(
        select(LogAccesso)
        .where(LogAccesso.utente_id == utente.id)
        .order_by(LogAccesso.id.desc())
        .limit(10)
    ).all()
    codici_rimasti = db.scalar(
        select(func.count())
        .select_from(CodiceRecupero)
        .where(CodiceRecupero.utente_id == utente.id, CodiceRecupero.usato_il.is_(None))
    )
    contesto = {
        "sessioni": sessioni,
        "sessione_corrente_id": sessione.id,
        "accessi": accessi,
        "codici_rimasti": codici_rimasti,
        "ok": MESSAGGI.get(request.query_params.get("ok", "")),
    }
    contesto.update(extra)
    return render(request, "auth/account.html", contesto, status_code=status_code)


@router.get("")
def account(request: Request, db: Db, utente: Autenticato, sessione: SessioneCorrente) -> Response:
    return _pagina(request, db, utente, sessione)


@router.post("/password", dependencies=[Depends(verifica_csrf)])
def cambia_password(
    request: Request,
    db: Db,
    utente: Autenticato,
    sessione: SessioneCorrente,
    attuale: Annotated[str, Form()],
    nuova: Annotated[str, Form()],
    conferma: Annotated[str, Form()],
) -> Response:
    errore = servizio.cambia_password(db, utente, attuale, nuova, conferma)
    if errore:
        db.rollback()
        return _pagina(request, db, utente, sessione, 400, errore_password=errore)
    db.execute(
        delete(SessioneModel).where(
            SessioneModel.utente_id == utente.id, SessioneModel.id != sessione.id
        )
    )
    audit.registra(db, utente, "utente", utente.id, "modifica", {"password": "modificata"})
    db.commit()
    return RedirectResponse("/account?ok=password", status_code=303)


@router.post("/codici-recupero", dependencies=[Depends(verifica_csrf)])
def nuovi_codici(
    request: Request,
    db: Db,
    utente: Autenticato,
    sessione: SessioneCorrente,
    codice: Annotated[str, Form()],
) -> Response:
    if not servizio.verifica_solo_totp(utente, codice):
        return _pagina(
            request, db, utente, sessione, 400, errore_codici="Codice dell'app non valido."
        )
    codici = servizio.rigenera_codici_recupero(utente)
    audit.registra(db, utente, "utente", utente.id, "modifica", {"codici_recupero": "rigenerati"})
    db.commit()
    return render(request, "auth/codici_recupero.html", {"codici": codici, "torna": "/account"})


@router.post("/esci-altrove", dependencies=[Depends(verifica_csrf)])
def esci_altrove(db: Db, utente: Autenticato, sessione: SessioneCorrente) -> Response:
    db.execute(
        delete(SessioneModel).where(
            SessioneModel.utente_id == utente.id, SessioneModel.id != sessione.id
        )
    )
    db.commit()
    return RedirectResponse("/account?ok=sessioni", status_code=303)
