"""PDF dei bandi da caricare a mano: quelli che la raccolta notturna non può scaricare
(il sito lo vieta nel robots.txt, o dà errore). Il consulente apre il link nel browser, salva il
PDF e lo carica qui: il testo viene letto subito e l'AI rianalizza il bando la notte dopo."""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.db import get_db
from app.documenti import servizio as s
from app.models import Bando, Utente
from app.models.documenti import DocumentoBando
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]


@router.get("/documenti/da-caricare")
def da_caricare(request: Request, db: Db, _: Autenticato) -> Response:
    return render(
        request,
        "documenti/da_caricare.html",
        {"righe": s.da_caricare(db), "vietato": s.VIETATO_ROBOTS},
    )


@router.post("/bandi/{bando_id}/documenti", dependencies=[Depends(verifica_csrf)])
def carica(
    bando_id: int,
    db: Db,
    utente: Autenticato,
    form: Form,
    file: Annotated[UploadFile, File()],
) -> Response:
    b = db.get(Bando, bando_id)
    if b is None:
        raise HTTPException(404)
    documento = None
    did = testo(form, "documento_id")
    if did and did.isdigit():
        documento = db.get(DocumentoBando, int(did))
        if documento is None or documento.bando_id != b.id:
            raise HTTPException(404)
    torna = "/documenti/da-caricare" if testo(form, "torna") == "elenco" else f"/bandi/{b.id}"
    nome = (file.filename or "documento.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    dati = file.file.read(s.MAX_MB * 1024 * 1024 + 1)
    try:
        doc = s.carica_a_mano(db, b.id, dati, nome, documento)
    except s.ErroreDocumento as e:
        db.rollback()
        return RedirectResponse(f"{torna}?esito=errore&msg={quote(str(e))}#documenti", 303)
    audit.registra(db, utente, "bando", b.id, "modifica", {"pdf_caricato": nome})
    db.commit()
    messaggio = (
        f"PDF «{nome}» caricato: {doc.pagine} pagine lette. L'AI lo userà alla prossima analisi."
        if doc.testo_estratto
        else f"PDF «{nome}» caricato, ma senza testo leggibile (scansione)."
    )
    return RedirectResponse(f"{torna}?esito=ok&msg={quote(messaggio)}#documenti", 303)


@router.post("/documenti/da-caricare", dependencies=[Depends(verifica_csrf)])
def carica_insieme(
    db: Db,
    utente: Autenticato,
    file: Annotated[list[UploadFile], File()],
) -> Response:
    """Più PDF insieme (es. tutti quelli scaricati dal browser): ognuno va al bando il cui
    link ha lo stesso nome di file."""
    per_nome = s.documenti_per_nome(db)
    caricati, non_abbinati, errori = 0, [], []
    for f in file[:50]:
        nome = (f.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        documenti = per_nome.get(s.nome_scaricato(nome), [])
        if not documenti:
            non_abbinati.append(nome)
            continue
        dati = f.file.read(s.MAX_MB * 1024 * 1024 + 1)
        for d in documenti:
            try:
                s.carica_a_mano(db, d.bando_id, dati, nome, d)
                audit.registra(db, utente, "bando", d.bando_id, "modifica", {"pdf_caricato": nome})
                db.commit()
                caricati += 1
            except s.ErroreDocumento as e:
                db.rollback()
                errori.append(f"{nome}: {e}")
    parti = [f"PDF caricati: {caricati}."]
    if non_abbinati:
        parti.append(
            "Non abbinati (il nome non corrisponde a nessun link): "
            + ", ".join(non_abbinati[:10])
            + ". Caricali dalla riga del bando."
        )
    if errori:
        parti.append("Errori: " + "; ".join(errori[:5]))
    esito = "ok" if caricati and not errori else "errore"
    return RedirectResponse(
        f"/documenti/da-caricare?esito={esito}&msg={quote(' '.join(parti))}", status_code=303
    )
