"""Pagina del cestino (solo titolare: /cestino non è in nessuna sezione assegnabile)."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.cestino import servizio as s
from app.db import get_db
from app.models import ElementoCestino, Utente
from app.web import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
CSRF = [Depends(verifica_csrf)]


def _elemento(db: Session, eid: int) -> ElementoCestino:
    e = db.get(ElementoCestino, eid)
    if e is None:
        raise HTTPException(404)
    return e


@router.get("/cestino")
def elenco(request: Request, db: Db, _: Autenticato) -> Response:
    elementi = list(
        db.scalars(
            select(ElementoCestino)
            .options(selectinload(ElementoCestino.eliminato_da))
            .order_by(ElementoCestino.creato_il.desc(), ElementoCestino.id.desc())
        )
    )
    return render(
        request,
        "cestino/elenco.html",
        {"elementi": elementi, "scade_il": s.scade_il, "giorni": s.GIORNI_CONSERVAZIONE},
    )


@router.post("/cestino/{eid}/ripristina", dependencies=CSRF)
def ripristina(eid: int, db: Db, utente: Autenticato) -> Response:
    e = _elemento(db, eid)
    descrizione, tipo, tabella, oggetto_id = e.descrizione, e.tipo, e.tabella, e.oggetto_id
    try:
        link = s.ripristina(db, e)
    except s.ErroreRipristino as err:
        db.rollback()
        return RedirectResponse(f"/cestino?esito=errore&msg={quote(str(err))}", 303)
    audit.registra(db, utente, tabella, oggetto_id, "crea", {"ripristinato": descrizione})
    db.commit()
    # la pagina mostra «… ripristinato» (avviso in base.html, valido su ogni pagina)
    avviso = ("ripristinato", f"{tipo.capitalize()} «{descrizione}»")
    destinazione = link or "/cestino"
    base, _, ancora = destinazione.partition("#")
    separatore = "&" if "?" in base else "?"
    return RedirectResponse(
        f"{base}{separatore}{avviso[0]}={quote(avviso[1])}" + (f"#{ancora}" if ancora else ""),
        303,
    )


@router.post("/cestino/{eid}/elimina", dependencies=CSRF)
def elimina(eid: int, db: Db, utente: Autenticato) -> Response:
    e = _elemento(db, eid)
    descrizione = e.descrizione
    audit.registra(db, utente, e.tabella, e.oggetto_id, "elimina", {"dal_cestino": descrizione})
    s.elimina_definitivamente(db, e)
    db.commit()
    msg = quote(f"«{descrizione}» eliminato per sempre.")
    return RedirectResponse(f"/cestino?esito=ok&msg={msg}", 303)


@router.post("/cestino/svuota", dependencies=CSRF)
def svuota(db: Db, utente: Autenticato) -> Response:
    elementi = list(db.scalars(select(ElementoCestino)))
    for e in elementi:
        s.elimina_definitivamente(db, e)
    audit.registra(db, utente, "cestino", 0, "elimina", {"svuotato": len(elementi)})
    db.commit()
    msg = quote(f"Cestino svuotato: {len(elementi)} elementi eliminati per sempre.")
    return RedirectResponse(f"/cestino?esito=ok&msg={msg}", 303)
