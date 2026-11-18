"""Pagine dell'import dei clienti da CSV (solo titolare): caricamento → mappatura e
anteprima → prova a vuoto → import → report degli errori."""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.db import get_db
from app.importer import csv_clienti as imp
from app.models import ImportJob, MappaturaImport, Utente
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
# Chi può importare lo decide il titolare (sezione «importazione», app/auth/permessi.py)
Titolare = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]
TIPI = {
    "aziende": "Aziende (con eventuale referente nella stessa riga)",
    "referenti": "Solo referenti",
}


def _job(db: Session, job_id: int) -> ImportJob:
    j = db.get(ImportJob, job_id)
    if j is None:
        raise HTTPException(404)
    return j


@router.get("/import")
def elenco(request: Request, db: Db, _: Titolare) -> Response:
    return render(
        request,
        "import/elenco.html",
        {
            "jobs": list(db.scalars(select(ImportJob).order_by(ImportJob.id.desc()).limit(30))),
            "tipi": TIPI,
            "mappature": list(db.scalars(select(MappaturaImport).order_by(MappaturaImport.nome))),
        },
    )


@router.post("/import", dependencies=CSRF)
def carica(db: Db, utente: Titolare, form: Form, file: Annotated[UploadFile, File()]) -> Response:
    dati = file.file.read(imp.MAX_BYTE + 1)
    nome = (file.filename or "clienti.csv").replace("\\", "/").rsplit("/", 1)[-1][:200]
    tipo = testo(form, "tipo") if testo(form, "tipo") in TIPI else "aziende"
    try:
        contenuto, codifica = imp.decodifica(dati)
        sep = imp.separatore(contenuto)
        intestazioni, righe = imp.leggi(contenuto, sep)
    except imp.ErroreImport as e:
        return RedirectResponse(f"/import?msg={quote(str(e))}", status_code=303)
    salvata = None
    mid = testo(form, "mappatura_id")
    if mid and mid.isdigit():
        m = db.get(MappaturaImport, int(mid))
        salvata = m.mappatura if m else None
    mappa = {h: c for h, c in (salvata or {}).items() if h in intestazioni} or (
        imp.indovina_mappatura(intestazioni)
    )
    job = ImportJob(
        nome_file=nome,
        contenuto=contenuto,
        separatore=sep,
        codifica=codifica,
        stato="caricato",
        mappatura={"tipo": tipo, "colonne": mappa},
        righe_totali=len(righe),
        righe_importate=0,
        righe_scartate=0,
        utente_id=utente.id,
    )
    db.add(job)
    db.commit()
    return RedirectResponse(f"/import/{job.id}", status_code=303)


@router.get("/import/{job_id}")
def scheda(request: Request, job_id: int, db: Db, _: Titolare) -> Response:
    j = _job(db, job_id)
    mappa = (j.mappatura or {}).get("colonne", {})
    tipo = (j.mappatura or {}).get("tipo", "aziende")
    intestazioni, righe = imp.anteprima(j.contenuto, j.separatore, mappa, tipo)
    return render(
        request,
        "import/scheda.html",
        {
            "j": j,
            "tipo": tipo,
            "tipi": TIPI,
            "intestazioni": intestazioni,
            "righe": righe,
            "mappa": mappa,
            "campi": imp.CAMPI,
            "mappature": list(db.scalars(select(MappaturaImport).order_by(MappaturaImport.nome))),
            "esito": request.query_params.get("esito"),
            "msg": request.query_params.get("msg"),
        },
    )


@router.post("/import/{job_id}/mappatura", dependencies=CSRF)
def salva_mappatura(job_id: int, db: Db, _: Titolare, form: Form) -> Response:
    j = _job(db, job_id)
    intestazioni, _r = imp.leggi(j.contenuto, j.separatore)
    mappa = {}
    for i, h in enumerate(intestazioni):
        campo = testo(form, f"col_{i}")
        if campo in imp.CAMPI and campo not in mappa.values():
            mappa[h] = campo
    tipo = testo(form, "tipo") if testo(form, "tipo") in TIPI else "aziende"
    j.mappatura = {"tipo": tipo, "colonne": mappa}
    nome = testo(form, "nome_mappatura")
    if nome:
        m = db.scalar(select(MappaturaImport).where(MappaturaImport.nome == nome[:100]))
        if m is None:
            db.add(MappaturaImport(nome=nome[:100], mappatura=mappa))
        else:
            m.mappatura = mappa
    db.commit()
    return RedirectResponse(f"/import/{j.id}#anteprima", status_code=303)


@router.post("/import/{job_id}/esegui", dependencies=CSRF)
def esegui(job_id: int, db: Db, utente: Titolare, form: Form) -> Response:
    j = _job(db, job_id)
    prova = testo(form, "modo") != "importa"
    mappa = (j.mappatura or {}).get("colonne", {})
    tipo = (j.mappatura or {}).get("tipo", "aziende")
    if "partita_iva" not in mappa.values():
        msg = "Collega una colonna alla partita IVA: serve a riconoscere i clienti."
        return RedirectResponse(f"/import/{j.id}?esito=errore&msg={quote(msg)}", status_code=303)
    try:
        esito = imp.importa(db, j.contenuto, j.separatore, mappa, tipo, prova)
    except Exception:
        db.rollback()
        raise
    if prova:
        db.rollback()
    j = _job(db, job_id)
    j.righe_totali = esito.totali
    j.righe_importate = esito.importate
    j.righe_scartate = esito.scartate
    j.errori = esito.errori
    j.stato = "provato" if prova else "importato"
    if not prova:
        audit.registra(
            db,
            utente,
            "import_job",
            j.id,
            "crea",
            {"file": j.nome_file, "nuove": esito.nuove, "aggiornate": esito.aggiornate},
        )
    db.commit()
    if prova:
        msg = (
            f"Prova a vuoto: {esito.importate} righe valide ({esito.nuove} nuovi clienti, "
            f"{esito.aggiornate} da aggiornare), {esito.scartate} con errori. "
            "Niente è stato salvato."
        )
    else:
        msg = (
            f"Import completato: {esito.nuove} nuovi clienti, {esito.aggiornate} aggiornati, "
            f"{esito.scartate} righe scartate."
        )
    return RedirectResponse(f"/import/{j.id}?esito=ok&msg={quote(msg)}", status_code=303)


@router.get("/import/{job_id}/errori.csv")
def errori(job_id: int, db: Db, _: Titolare) -> Response:
    j = _job(db, job_id)
    return Response(
        imp.csv_errori(j.errori or []),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="errori-import-{j.id}.csv"'},
    )
