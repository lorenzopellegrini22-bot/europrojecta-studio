"""Segnalatori: chi porta clienti allo studio, con provvigione stimata sui contributi concessi.

Provvigione stimata = importo concesso delle pratiche dei clienti segnalati × percentuale.
È una stima per il consulente, non un documento contabile."""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.models import Azienda, Pratica, Segnalatore, Utente
from app.pratiche.servizio import ETICHETTE_STATO_PRATICA
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]


def _percentuale(valore: str | None) -> Decimal | None | str:
    if not valore:
        return None
    try:
        p = Decimal(valore.replace("%", "").replace(",", ".").strip())
    except InvalidOperation:
        return "non valida"
    return p if 0 <= p <= 100 else "non valida"


def riepilogo(db: Session) -> dict[int, dict[str, Any]]:
    """Per ogni segnalatore: clienti, pratiche, importi concessi e provvigione stimata."""
    righe = db.execute(
        select(
            Azienda.segnalatore_id,
            func.count(func.distinct(Azienda.id)),
            func.count(Pratica.id),
            func.coalesce(func.sum(Pratica.importo_concesso), 0),
        )
        .outerjoin(Pratica, Pratica.azienda_id == Azienda.id)
        .where(Azienda.segnalatore_id.is_not(None))
        .group_by(Azienda.segnalatore_id)
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for sid, clienti, pratiche, concesso in righe:
        out[sid] = {"clienti": clienti, "pratiche": pratiche, "concesso": Decimal(concesso)}
    for s in db.scalars(select(Segnalatore)):
        d = out.setdefault(s.id, {"clienti": 0, "pratiche": 0, "concesso": Decimal(0)})
        d["provvigione"] = (
            (d["concesso"] * s.provvigione_percentuale / 100).quantize(Decimal("0.01"))
            if s.provvigione_percentuale is not None
            else None
        )
    return out


def _valori(form: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    v = {
        "nome": testo(form, "nome"),
        "organizzazione": testo(form, "organizzazione"),
        "email": testo(form, "email"),
        "telefono": testo(form, "telefono"),
        "provvigione_percentuale": _percentuale(testo(form, "provvigione_percentuale")),
        "note": testo(form, "note"),
    }
    errori: dict[str, str] = {}
    if not v["nome"]:
        errori["nome"] = "Il nome è obbligatorio."
    if v["provvigione_percentuale"] == "non valida":
        errori["provvigione_percentuale"] = "Percentuale tra 0 e 100."
    if v["email"] and "@" not in v["email"]:
        errori["email"] = "Email non valida."
    return v, errori


@router.get("/segnalatori")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    segnalatori = list(db.scalars(select(Segnalatore).order_by(Segnalatore.nome)))
    return render(
        request,
        "segnalatori/lista.html",
        {"segnalatori": segnalatori, "dati": riepilogo(db), "v": {}, "errori": {}},
    )


@router.post("/segnalatori", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    v, errori = _valori(form)
    if errori:
        segnalatori = list(db.scalars(select(Segnalatore).order_by(Segnalatore.nome)))
        return render(
            request,
            "segnalatori/lista.html",
            {"segnalatori": segnalatori, "dati": riepilogo(db), "v": v, "errori": errori},
            400,
        )
    s = Segnalatore(**v)
    db.add(s)
    db.flush()
    audit.registra(db, utente, "segnalatore", s.id, "crea", {"nome": s.nome})
    db.commit()
    return RedirectResponse(f"/segnalatori/{s.id}", status_code=303)


def _segnalatore(db: Session, sid: int) -> Segnalatore:
    s = db.get(Segnalatore, sid)
    if s is None:
        raise HTTPException(404)
    return s


@router.get("/segnalatori/{sid}")
def scheda(request: Request, sid: int, db: Db, _: Autenticato) -> Response:
    s = _segnalatore(db, sid)
    clienti = list(
        db.scalars(
            select(Azienda).where(Azienda.segnalatore_id == s.id).order_by(Azienda.ragione_sociale)
        )
    )
    pratiche = list(
        db.scalars(
            select(Pratica)
            .join(Azienda)
            .where(Azienda.segnalatore_id == s.id)
            .options(selectinload(Pratica.azienda), selectinload(Pratica.bando))
            .order_by(Pratica.creato_il.desc())
        )
    )
    return render(
        request,
        "segnalatori/scheda.html",
        {
            "s": s,
            "clienti": clienti,
            "pratiche": pratiche,
            "dati": riepilogo(db).get(s.id),
            "etichette_stato": ETICHETTE_STATO_PRATICA,
            "errori": {},
        },
    )


@router.post("/segnalatori/{sid}", dependencies=CSRF)
def salva(request: Request, sid: int, db: Db, utente: Autenticato, form: Form) -> Response:
    s = _segnalatore(db, sid)
    v, errori = _valori(form)
    if errori:
        return render(
            request,
            "errore.html",
            {"messaggio": " ".join(errori.values())},
            400,
        )
    for k, val in v.items():
        setattr(s, k, val)
    s.attivo = form.get("attivo") == "1"
    audit.registra(db, utente, "segnalatore", s.id, "modifica", {"nome": s.nome})
    db.commit()
    return RedirectResponse(f"/segnalatori/{s.id}", status_code=303)


@router.post("/segnalatori/{sid}/elimina", dependencies=CSRF)
def elimina(sid: int, db: Db, utente: Titolare) -> Response:
    """I clienti segnalati restano: perdono solo il collegamento al segnalatore."""
    s = _segnalatore(db, sid)
    audit.registra(db, utente, "segnalatore", s.id, "elimina", {"nome": s.nome})
    from app.cestino import servizio as cestino

    e = cestino.sposta(db, utente, s, "segnalatore", s.nome, f"/segnalatori/{s.id}")
    db.delete(s)
    db.commit()
    return RedirectResponse(f"/segnalatori?cestino={e.id}", status_code=303)
