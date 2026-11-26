"""Pagine degli aggiornamenti ai clienti: bozze, revisione, approvazione e invio; pagina
pubblica di disiscrizione (raggiunta dal link nelle email)."""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.aggiornamenti import servizio as s
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.config import get_settings
from app.db import get_db
from app.models import AggiornamentoCliente, Azienda, Utente
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]
ETICHETTE = {
    "bozza": "Bozza",
    "approvato": "Approvato",
    "inviato": "Inviato",
    "errore": "Non inviato",
    "scartato": "Scartato",
}


def _ag(db: Session, ag_id: int) -> AggiornamentoCliente:
    ag = db.get(
        AggiornamentoCliente,
        ag_id,
        options=[
            selectinload(AggiornamentoCliente.azienda),
            selectinload(AggiornamentoCliente.referente),
        ],
    )
    if ag is None:
        raise HTTPException(404)
    return ag


def _base_url() -> str:
    return get_settings().url_pubblico.rstrip("/")


def _approva_e_invia(db: Session, ag: AggiornamentoCliente, utente: Utente) -> s.Esito:
    ag.stato, ag.approvato_da_id, ag.approvato_il = "approvato", utente.id, datetime.now(UTC)
    esito = s.invia(db, ag, utente, _base_url())
    audit.registra(
        db,
        utente,
        "aggiornamento_cliente",
        ag.id,
        "invia",
        {"azienda": ag.azienda.ragione_sociale, "canale": ag.canale, "esito": ag.stato},
    )
    db.commit()
    return esito


@router.get("/aggiornamenti")
def elenco(request: Request, db: Db, _: Autenticato) -> Response:
    stato = request.query_params.get("stato") or "bozza"
    query = (
        select(AggiornamentoCliente)
        .options(
            selectinload(AggiornamentoCliente.azienda),
            selectinload(AggiornamentoCliente.referente),
            selectinload(AggiornamentoCliente.automazione),
        )
        .order_by(AggiornamentoCliente.creato_il.desc())
        .limit(200)
    )
    if stato in ETICHETTE:
        query = query.where(AggiornamentoCliente.stato == stato)
    return render(
        request,
        "aggiornamenti/elenco.html",
        {
            "elenco": list(db.scalars(query)),
            "stato": stato,
            "etichette": ETICHETTE,
            "msg": request.query_params.get("msg"),
            "esito": request.query_params.get("esito"),
        },
    )


@router.post("/aggiornamenti/genera", dependencies=CSRF)
def genera_tutte(db: Db, utente: Autenticato, form: Form) -> Response:
    """Una bozza per ogni azienda attiva con almeno un referente che ha dato il consenso."""
    usa_ai = form.get("usa_ai") == "1"
    create = senza_consenso = 0
    for a in db.scalars(
        select(Azienda)
        .where(Azienda.stato_cliente == "attivo")
        .options(selectinload(Azienda.referenti))
    ):
        if s.crea_bozza(db, a, usa_ai=usa_ai) is None:
            senza_consenso += 1
        else:
            create += 1
    db.commit()
    msg = f"Bozze create: {create}."
    if senza_consenso:
        msg += f" {senza_consenso} aziende senza referenti con il consenso agli aggiornamenti."
    return RedirectResponse(f"/aggiornamenti?esito=ok&msg={quote(msg)}", status_code=303)


@router.post("/clienti/{azienda_id}/aggiornamenti", dependencies=CSRF)
def genera_una(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = db.get(Azienda, azienda_id, options=[selectinload(Azienda.referenti)])
    if a is None:
        raise HTTPException(404)
    bozza = s.crea_bozza(db, a, usa_ai=form.get("usa_ai") == "1")
    db.commit()
    if bozza is None:
        msg = "Nessun referente ha dato il consenso agli aggiornamenti: registralo nella scheda."
        return RedirectResponse(f"/clienti/{a.id}?msg={quote(msg)}#referenti", status_code=303)
    return RedirectResponse(f"/aggiornamenti/{bozza.id}", status_code=303)


@router.get("/aggiornamenti/{ag_id}")
def scheda(request: Request, ag_id: int, db: Db, utente: Autenticato) -> Response:
    ag = _ag(db, ag_id)
    oggetto, anteprima = s.testo_finale(ag, utente, _base_url())
    referenti = [
        r
        for r in ag.azienda.referenti
        if not r.disiscritto_il and (r.consenso_email or r.consenso_whatsapp)
    ]
    return render(
        request,
        "aggiornamenti/scheda.html",
        {
            "ag": ag,
            "anteprima_oggetto": oggetto,
            "anteprima": anteprima,
            "referenti": referenti,
            "etichette": ETICHETTE,
            "msg": request.query_params.get("msg"),
            "esito": request.query_params.get("esito"),
        },
    )


@router.post("/aggiornamenti/{ag_id}", dependencies=CSRF)
def azione(ag_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    ag = _ag(db, ag_id)
    if ag.stato == "inviato":
        return RedirectResponse(f"/aggiornamenti/{ag.id}", status_code=303)
    scelta = testo(form, "azione") or "salva"
    if scelta in ("salva", "invia"):
        ag.oggetto = (testo(form, "oggetto") or ag.oggetto)[:300]
        ag.testo = testo(form, "testo") or ag.testo
        rid = testo(form, "referente_canale")
        if rid and ":" in rid:
            ref_id, canale = rid.split(":", 1)
            ref = next((r for r in ag.azienda.referenti if str(r.id) == ref_id), None)
            if ref is not None and canale in ("email", "whatsapp"):
                ag.referente_id, ag.canale = ref.id, canale
                db.flush()
                db.refresh(ag)
        if ag.stato == "errore":
            ag.stato = "bozza"
    if scelta == "scarta":
        ag.stato = "scartato"
        db.commit()
        return RedirectResponse("/aggiornamenti", status_code=303)
    if scelta == "invia":
        esito = _approva_e_invia(db, ag, utente)
        msg = "Aggiornamento inviato." if esito.ok else f"Non inviato: {esito.errore}"
        return RedirectResponse(
            f"/aggiornamenti/{ag.id}?esito={'ok' if esito.ok else 'errore'}&msg={quote(msg)}",
            status_code=303,
        )
    db.commit()
    return RedirectResponse(f"/aggiornamenti/{ag.id}?esito=ok&msg=Bozza+salvata", status_code=303)


@router.post("/aggiornamenti-approva", dependencies=CSRF)
def approva_selezionati(db: Db, utente: Autenticato, form: Form) -> Response:
    """Approva e invia più bozze insieme dalla schermata di revisione."""
    ids = form.get("ids") or []
    ids = [ids] if isinstance(ids, str) else ids
    inviati = errori = 0
    for x in ids:
        if not str(x).isdigit():
            continue
        ag = _ag(db, int(x))
        if ag.stato not in ("bozza", "errore"):
            continue
        esito = _approva_e_invia(db, ag, utente)
        inviati += esito.ok
        errori += not esito.ok
    msg = f"Inviati: {inviati}." + (
        f" Non inviati: {errori} (vedi «Non inviati»)." if errori else ""
    )
    return RedirectResponse(
        f"/aggiornamenti?esito={'ok' if not errori else 'errore'}&msg={quote(msg)}",
        status_code=303,
    )


# --- Disiscrizione (pubblica) -------------------------------------------------------------


@router.get("/disiscrizione/{token}")
def disiscrizione(request: Request, token: str, db: Db) -> Response:
    r = s.referente_da_token(db, token)
    if r is None:
        return render(request, "aggiornamenti/disiscrizione.html", {"valido": False}, 404)
    return render(
        request,
        "aggiornamenti/disiscrizione.html",
        {"valido": True, "token": token, "fatto": r.disiscritto_il is not None},
    )


@router.post("/disiscrizione/{token}")
def conferma_disiscrizione(request: Request, token: str, db: Db) -> Response:
    """Niente CSRF di sessione: il destinatario non ha un account; il token firmato basta
    (serve solo a togliere un consenso, mai a darlo)."""
    r = s.referente_da_token(db, token)
    if r is None:
        return render(request, "aggiornamenti/disiscrizione.html", {"valido": False}, 404)
    if r.disiscritto_il is None:
        r.disiscritto_il = datetime.now(UTC)
        r.consenso_email = r.consenso_whatsapp = False
        audit.registra(db, None, "referente", r.id, "modifica", {"disiscritto": True})
        db.commit()
    return render(
        request, "aggiornamenti/disiscrizione.html", {"valido": True, "token": token, "fatto": True}
    )
