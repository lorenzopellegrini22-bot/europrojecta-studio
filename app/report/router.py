"""Pagine del report PDF su un bando: bozza modificabile, approvazione, PDF."""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.db import get_db
from app.models import Azienda, Bando, ReportBando, Utente
from app.report import servizio as s
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]


def _report(db: Session, rid: int) -> ReportBando:
    r = db.get(ReportBando, rid)
    if r is None:
        raise HTTPException(404)
    return r


@router.post("/clienti/{azienda_id}/report/{bando_id}", dependencies=CSRF)
def crea(azienda_id: int, bando_id: int, db: Db, utente: Autenticato) -> Response:
    a, b = db.get(Azienda, azienda_id), db.get(Bando, bando_id)
    if a is None or b is None:
        raise HTTPException(404)
    r = s.apri_o_crea(db, a, b)
    db.commit()
    return RedirectResponse(f"/report/{r.id}", status_code=303)


@router.get("/report/{rid}")
def scheda(request: Request, rid: int, db: Db, _: Autenticato) -> Response:
    r = _report(db, rid)
    return render(
        request,
        "report/scheda.html",
        {
            "r": r,
            "b": db.get(Bando, r.bando_id),
            "sezioni": s.sezioni(r.azienda),
            "msg": request.query_params.get("msg"),
            "esito": request.query_params.get("esito"),
        },
    )


@router.post("/report/{rid}", dependencies=CSRF)
def salva(rid: int, db: Db, utente: Autenticato, form: Form) -> Response:
    r = _report(db, rid)
    azione = testo(form, "azione") or "salva"
    if azione == "riapri":
        r.stato, r.approvato_il = "bozza", None
    elif azione == "inviato":
        r.stato = "inviato"
        audit.registra(db, utente, "report_bando", r.id, "invia", {"bando_id": r.bando_id})
    else:
        if r.stato == "bozza":
            s.aggiorna(r, form)
        if azione == "approva":
            r.stato, r.approvato_il = "approvato", datetime.now(UTC)
    db.commit()
    messaggio = {
        "approva": "Report approvato: ora si scarica in PDF e in Word.",
        "riapri": "Report riaperto: puoi modificarlo.",
        "inviato": "Report segnato come inviato.",
    }.get(azione, "Bozza salvata.")
    return RedirectResponse(f"/report/{r.id}?esito=ok&msg={quote(messaggio)}", status_code=303)


@router.get("/report/{rid}/pdf")
def scarica_pdf(rid: int, db: Db, utente: Autenticato) -> Response:
    r = _report(db, rid)
    if r.stato == "bozza":
        msg = quote("Approva il report prima di generare il PDF.")
        return RedirectResponse(f"/report/{r.id}?esito=errore&msg={msg}", status_code=303)
    b = db.get(Bando, r.bando_id)
    try:
        dati = s.pdf(r, b, utente.nome)
    except s.ErrorePDF as e:
        return RedirectResponse(f"/report/{r.id}?esito=errore&msg={quote(str(e))}", status_code=303)
    db.commit()
    nome = f"bando-{b.id}-{r.azienda.ragione_sociale[:30]}".replace(" ", "-").replace('"', "")
    return Response(
        dati,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome}.pdf"'},
    )


@router.get("/report/{rid}/word")
def scarica_word(rid: int, db: Db, utente: Autenticato) -> Response:
    """Il report in Word, per ritoccarlo prima dell'invio (dopo l'approvazione, come il PDF)."""
    r = _report(db, rid)
    if r.stato == "bozza":
        msg = quote("Approva il report prima di scaricarlo.")
        return RedirectResponse(f"/report/{r.id}?esito=errore&msg={msg}", status_code=303)
    b = db.get(Bando, r.bando_id)
    nome = f"bando-{b.id}-{r.azienda.ragione_sociale[:30]}".replace(" ", "-")
    nome = "".join(c for c in nome if c.isalnum() or c in "-_.") or "report"
    return Response(
        s.word(r, b, utente.nome),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{nome}.docx"'},
    )
