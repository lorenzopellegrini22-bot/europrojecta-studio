"""Impostazioni → Integrazioni. Solo il titolare può vedere e modificare le chiavi."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.auth.dipendenze import richiede_titolare, verifica_csrf
from app.db import get_db
from app.integrazioni import servizio
from app.integrazioni.registro import INTEGRAZIONI, TipoIntegrazione
from app.integrazioni.verifiche import VERIFICHE
from app.models import Utente
from app.sicurezza.cifratura import ErroreCifratura
from app.web import render

router = APIRouter(prefix="/impostazioni")

Db = Annotated[Session, Depends(get_db)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]


def _tipo(codice: str) -> TipoIntegrazione:
    tipo = INTEGRAZIONI.get(codice)
    if tipo is None:
        raise HTTPException(status_code=404)
    return tipo


@router.get("")
def impostazioni(_: Titolare) -> Response:
    return RedirectResponse("/impostazioni/integrazioni", status_code=303)


@router.get("/integrazioni")
def elenco(request: Request, db: Db, _: Titolare) -> Response:
    stati = [servizio.stato(db, codice) for codice in INTEGRAZIONI]
    return render(request, "impostazioni/integrazioni.html", {"stati": stati})


@router.get("/integrazioni/{codice}")
def dettaglio(request: Request, db: Db, _: Titolare, codice: str) -> Response:
    _tipo(codice)
    salvato = request.query_params.get("salvato") == "1"
    return render(
        request,
        "impostazioni/integrazione.html",
        {"stato": servizio.stato(db, codice), "errori": {}, "salvato": salvato},
    )


async def _form(request: Request) -> FormData:
    """Il form letto in modo asincrono, così l'endpoint può restare sincrono."""
    return await request.form()


@router.post("/integrazioni/{codice}", dependencies=[Depends(verifica_csrf)])
def salva(
    request: Request,
    db: Db,
    utente: Titolare,
    codice: str,
    form: Annotated[FormData, Depends(_form)],
) -> Response:
    tipo = _tipo(codice)
    dati = {c.nome: str(form.get(c.nome) or "") for c in tipo.campi}
    da_rimuovere = {c.nome for c in tipo.campi if c.segreto and form.get(f"rimuovi_{c.nome}")}
    try:
        servizio.salva(db, codice, dati, da_rimuovere, utente)
    except servizio.ErroreValidazione as e:
        return render(
            request,
            "impostazioni/integrazione.html",
            {
                "stato": servizio.stato(db, codice),
                "errori": e.errori,
                "salvato": False,
                "dati": dati,
            },
            status_code=400,
        )
    return RedirectResponse(f"/impostazioni/integrazioni/{codice}?salvato=1", status_code=303)


@router.post("/integrazioni/{codice}/verifica", dependencies=[Depends(verifica_csrf)])
def verifica(request: Request, db: Db, _: Titolare, codice: str) -> Response:
    """Chiamata da HTMX: restituisce solo il riquadro con l'esito."""
    _tipo(codice)
    funzione = VERIFICHE.get(codice)
    if funzione is None:
        ok, messaggio = False, "Per questa integrazione la verifica non è ancora disponibile."
    else:
        try:
            esito = funzione(servizio.leggi_valori(db, codice))
            ok, messaggio = esito.ok, esito.messaggio
        except ErroreCifratura:
            ok, messaggio = False, "Una chiave salvata non si può decifrare: reinseriscila."
        servizio.registra_verifica(db, codice, ok, messaggio)
    return render(
        request,
        "impostazioni/_esito_verifica.html",
        {"stato": servizio.stato(db, codice), "ok": ok, "messaggio": messaggio},
    )
