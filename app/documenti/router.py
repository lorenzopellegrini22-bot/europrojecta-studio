"""PDF dei bandi da caricare a mano: quelli che la raccolta notturna non può scaricare
(il sito lo vieta nel robots.txt, o dà errore). Il consulente apre il link nel browser, salva il
PDF e lo carica qui: il testo viene letto subito e l'AI rianalizza il bando la notte dopo."""

import contextlib
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
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
        f"PDF «{nome}» caricato: {doc.pagine} "
        f"{'pagina letta' if doc.pagine == 1 else 'pagine lette'}. "
        "L'AI lo userà alla prossima analisi."
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
    chiavi = [
        s.nome_scaricato((f.filename or "").replace("\\", "/").rsplit("/", 1)[-1])
        for f in file[:50]
    ]
    for f, chiave in zip(file[:50], chiavi, strict=True):
        nome = (f.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if chiavi.count(chiave) > 1:  # «bando.pdf» e «bando (1).pdf»: quale dei due?
            errori.append(f"{nome}: più file con lo stesso nome, caricalo dalla riga del bando")
            continue
        documenti = per_nome.get(chiave, [])
        if not documenti:
            non_abbinati.append(nome)
            continue
        dati = f.file.read(s.MAX_MB * 1024 * 1024 + 1)
        letto = None
        for d in documenti:
            try:
                if (
                    letto is None
                    and len(dati) <= s.MAX_MB * 1024 * 1024
                    and dati[:1024].lstrip().startswith(b"%PDF")
                ):
                    letto = s.testo_pdf(dati, db)
                s.carica_a_mano(db, d.bando_id, dati, nome, d, letto)
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


@router.post(
    "/bandi/{bando_id}/documenti/{documento_id}/elimina", dependencies=[Depends(verifica_csrf)]
)
def elimina(bando_id: int, documento_id: int, db: Db, utente: Autenticato) -> Response:
    """Toglie un PDF caricato a mano per sbaglio: l'AI non lo legge più."""
    d = db.get(DocumentoBando, documento_id)
    if d is None or d.bando_id != bando_id or not d.url.startswith("caricato-a-mano:"):
        raise HTTPException(404)
    if d.percorso:
        base = s.cartella().resolve()
        file = (base / d.percorso).resolve()
        altri = db.scalar(
            select(func.count())
            .select_from(DocumentoBando)
            .where(DocumentoBando.percorso == d.percorso, DocumentoBando.id != d.id)
        )
        # lo stesso PDF può servire a più bandi (caricamento insieme): il file resta finché serve
        if file.is_relative_to(base) and not altri:
            with contextlib.suppress(OSError):
                file.unlink(missing_ok=True)
                for copia in file.parent.glob(f"{file.stem}.*.docx"):
                    copia.unlink(missing_ok=True)
    nome = d.titolo or "documento"
    db.delete(d)
    audit.registra(db, utente, "bando", bando_id, "modifica", {"pdf_eliminato": nome})
    db.commit()
    messaggio = f"PDF «{nome}» eliminato: l'AI non lo userà più."
    return RedirectResponse(f"/bandi/{bando_id}?esito=ok&msg={quote(messaggio)}#documenti", 303)


@router.get("/bandi/{bando_id}/documenti/{documento_id}/word")
def scarica_word(bando_id: int, documento_id: int, db: Db, _: Autenticato) -> Response:
    """Il PDF del bando in Word (.docx), da modificare con Word o Google Documenti."""
    from app.documenti import word

    d = db.get(DocumentoBando, documento_id)
    if d is None or d.bando_id != bando_id or not (d.percorso or d.testo_estratto):
        raise HTTPException(404)
    dati, contenuto, copia = None, None, None
    if d.percorso:
        base = s.cartella().resolve()
        file = (base / d.percorso).resolve()
        if file.is_relative_to(base) and file.is_file():
            # i PDF lunghi richiedono qualche secondo: il Word si tiene accanto al PDF
            copia = file.with_name(f"{file.stem}.{word.VERSIONE}.docx")
            if copia.is_file():
                contenuto = copia.read_bytes()
            else:
                dati = file.read_bytes()
    if contenuto is None:
        if dati is None and not d.testo_estratto:
            raise HTTPException(404)
        contenuto = word.in_word(dati, d.titolo or "Documento del bando", d.testo_estratto)
        if copia is not None and dati is not None:
            # cartella di sola lettura: si rigenera ogni volta
            with contextlib.suppress(OSError):
                copia.write_bytes(contenuto)
    nome = word.nome_file(d.titolo)
    return Response(
        contenuto,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"documento.docx\"; filename*=UTF-8''{quote(nome)}"
            )
        },
    )


@router.get("/bandi/{bando_id}/documenti/{documento_id}")
def scarica(bando_id: int, documento_id: int, db: Db, _: Autenticato) -> Response:
    """PDF salvato nell'app (caricato a mano): si scarica da qui, non c'è un link all'ente."""
    d = db.get(DocumentoBando, documento_id)
    if d is None or d.bando_id != bando_id or not d.percorso:
        raise HTTPException(404)
    base = s.cartella().resolve()
    file = (base / d.percorso).resolve()
    if not file.is_relative_to(base) or not file.is_file():
        raise HTTPException(404)
    nome = "".join(c for c in (d.titolo or "documento") if c.isalnum() or c in " ._-")[:80]
    if not nome.lower().endswith(".pdf"):
        nome += ".pdf"
    return FileResponse(file, media_type="application/pdf", filename=nome)
