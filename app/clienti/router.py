"""Clienti: elenco, scheda, inserimento e modifica, referenti.

Titolare e collaboratori gestiscono i clienti; solo il titolare può eliminarli (e solo se non
hanno pratiche)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.models import Azienda, Pratica, Referente, Sede, Utente
from app.models.crm import DIMENSIONI, STATI_CLIENTE
from app.pratiche.servizio import ETICHETTE_STATO_PRATICA, partita_iva_valida
from app.utils.territorio import REGIONI
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

CAMPI_AZIENDA = (
    "ragione_sociale",
    "partita_iva",
    "codice_fiscale",
    "forma_giuridica",
    "dimensione_manuale",
    "pec",
    "email",
    "telefono",
    "codice_sdi",
    "stato_cliente",
    "note",
)
CAMPI_SEDE = ("indirizzo", "cap", "comune_testo", "provincia", "regione")


def _azienda(db: Session, azienda_id: int) -> Azienda:
    a = db.get(Azienda, azienda_id, options=[selectinload(Azienda.referenti)])
    if a is None:
        raise HTTPException(404)
    return a


def _valida(db: Session, valori: dict[str, Any], azienda_id: int | None) -> dict[str, str]:
    errori: dict[str, str] = {}
    if not valori.get("ragione_sociale"):
        errori["ragione_sociale"] = "La ragione sociale è obbligatoria."
    piva = valori.get("partita_iva")
    if piva:
        if not partita_iva_valida(piva):
            errori["partita_iva"] = "Partita IVA non valida (11 cifre con cifra di controllo)."
        else:
            altra = db.scalar(select(Azienda).where(Azienda.partita_iva == piva))
            if altra is not None and altra.id != azienda_id:
                errori["partita_iva"] = f"Partita IVA già usata da «{altra.ragione_sociale}»."
    cf = valori.get("codice_fiscale")
    if cf and len(cf) not in (11, 16):
        errori["codice_fiscale"] = "Il codice fiscale ha 11 o 16 caratteri."
    sdi = valori.get("codice_sdi")
    if sdi and len(sdi) != 7:
        errori["codice_sdi"] = "Il codice SDI ha 7 caratteri."
    cap = valori.get("cap")
    if cap and not (cap.isdigit() and len(cap) == 5):
        errori["cap"] = "Il CAP ha 5 cifre."
    if valori.get("stato_cliente") not in STATI_CLIENTE:
        errori["stato_cliente"] = "Stato non valido."
    if valori.get("dimensione_manuale") and valori["dimensione_manuale"] not in DIMENSIONI:
        errori["dimensione_manuale"] = "Dimensione non valida."
    if valori.get("regione") and valori["regione"] not in REGIONI:
        errori["regione"] = "Regione non valida."
    return errori


def _valori(form: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {c: testo(form, c) for c in CAMPI_AZIENDA + CAMPI_SEDE}
    for c in ("partita_iva", "codice_fiscale", "codice_sdi", "provincia"):
        if v[c]:
            v[c] = v[c].replace(" ", "").upper()
    if v["partita_iva"] and v["partita_iva"].startswith("IT"):
        v["partita_iva"] = v["partita_iva"][2:]
    v["stato_cliente"] = v["stato_cliente"] or "attivo"
    return v


def _form(
    request: Request, azienda: Azienda | None, valori: dict[str, Any], errori: dict[str, str]
) -> Response:
    return render(
        request,
        "clienti/form.html",
        {
            "azienda": azienda,
            "v": valori,
            "errori": errori,
            "regioni": REGIONI,
            "dimensioni": DIMENSIONI,
            "stati": STATI_CLIENTE,
        },
        400 if errori else 200,
    )


def _applica(azienda: Azienda, v: dict[str, Any]) -> dict[str, Any]:
    differenze: dict[str, Any] = {}
    for c in CAMPI_AZIENDA:
        if getattr(azienda, c) != v[c]:
            differenze[c] = v[c]
            setattr(azienda, c, v[c])
    sede = azienda.sede_legale
    if sede is None and any(v[c] for c in CAMPI_SEDE):
        sede = Sede(tipo="legale")
        azienda.sedi.append(sede)
    if sede is not None:
        for c in CAMPI_SEDE:
            if getattr(sede, c) != v[c]:
                differenze[f"sede_{c}"] = v[c]
                setattr(sede, c, v[c])
    return differenze


@router.get("/clienti")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    q = (request.query_params.get("q") or "").strip()
    stato = request.query_params.get("stato") or ""
    query = select(Azienda).options(selectinload(Azienda.sedi)).order_by(Azienda.ragione_sociale)
    if q:
        m = f"%{q}%"
        query = query.where(
            or_(
                Azienda.ragione_sociale.ilike(m),
                Azienda.partita_iva.ilike(m),
                Azienda.codice_fiscale.ilike(m),
                Azienda.email.ilike(m),
            )
        )
    if stato in STATI_CLIENTE:
        query = query.where(Azienda.stato_cliente == stato)
    aziende = list(db.scalars(query))
    conteggi = dict(
        db.execute(select(Pratica.azienda_id, func.count()).group_by(Pratica.azienda_id)).all()
    )
    return render(
        request,
        "clienti/lista.html",
        {
            "aziende": aziende,
            "conteggi": conteggi,
            "filtri": {"q": q, "stato": stato},
            "stati": STATI_CLIENTE,
        },
    )


@router.get("/clienti/nuovo")
def nuovo(request: Request, _: Autenticato) -> Response:
    return _form(request, None, {"stato_cliente": "attivo"}, {})


@router.post("/clienti/nuovo", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    v = _valori(form)
    errori = _valida(db, v, None)
    if errori:
        return _form(request, None, v, errori)
    azienda = Azienda(ragione_sociale=v["ragione_sociale"])
    _applica(azienda, v)
    db.add(azienda)
    db.flush()
    referente = testo(form, "referente_nome")
    if referente:
        azienda.referenti.append(
            Referente(
                nome=referente,
                cognome=testo(form, "referente_cognome"),
                email=testo(form, "referente_email"),
                telefono=testo(form, "referente_telefono"),
                principale=True,
            )
        )
    audit.registra(
        db, utente, "azienda", azienda.id, "crea", {"ragione_sociale": v["ragione_sociale"]}
    )
    db.commit()
    return RedirectResponse(f"/clienti/{azienda.id}", status_code=303)


@router.get("/clienti/{azienda_id}")
def scheda(request: Request, azienda_id: int, db: Db, _: Autenticato) -> Response:
    azienda = _azienda(db, azienda_id)
    pratiche = list(
        db.scalars(
            select(Pratica)
            .where(Pratica.azienda_id == azienda_id)
            .options(selectinload(Pratica.bando), selectinload(Pratica.fasi))
            .order_by(Pratica.creato_il.desc())
        )
    )
    return render(
        request,
        "clienti/scheda.html",
        {"azienda": azienda, "pratiche": pratiche, "etichette_stato": ETICHETTE_STATO_PRATICA},
    )


@router.get("/clienti/{azienda_id}/modifica")
def modifica(request: Request, azienda_id: int, db: Db, _: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    sede = a.sede_legale
    v = {c: getattr(a, c) for c in CAMPI_AZIENDA}
    v.update({c: getattr(sede, c) if sede else None for c in CAMPI_SEDE})
    return _form(request, a, v, {})


@router.post("/clienti/{azienda_id}/modifica", dependencies=CSRF)
def salva(request: Request, azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    v = _valori(form)
    errori = _valida(db, v, a.id)
    if errori:
        return _form(request, a, v, errori)
    differenze = _applica(a, v)
    if differenze:
        audit.registra(db, utente, "azienda", a.id, "modifica", differenze)
    db.commit()
    return RedirectResponse(f"/clienti/{a.id}", status_code=303)


@router.post("/clienti/{azienda_id}/elimina", dependencies=CSRF)
def elimina(request: Request, azienda_id: int, db: Db, utente: Titolare) -> Response:
    a = _azienda(db, azienda_id)
    if db.scalar(select(func.count()).select_from(Pratica).where(Pratica.azienda_id == a.id)):
        return render(
            request,
            "errore.html",
            {"messaggio": "Il cliente ha delle pratiche: elimina prima quelle."},
            400,
        )
    audit.registra(db, utente, "azienda", a.id, "elimina", {"ragione_sociale": a.ragione_sociale})
    db.delete(a)
    db.commit()
    return RedirectResponse("/clienti", status_code=303)


@router.post("/clienti/{azienda_id}/referenti", dependencies=CSRF)
def aggiungi_referente(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    nome = testo(form, "nome")
    if nome:
        principale = not a.referenti or form.get("principale") == "1"
        if principale:
            for r in a.referenti:
                r.principale = False
        ref = Referente(
            nome=nome,
            cognome=testo(form, "cognome"),
            ruolo=testo(form, "ruolo"),
            email=testo(form, "email"),
            telefono=testo(form, "telefono"),
            principale=principale,
        )
        a.referenti.append(ref)
        audit.registra(db, utente, "azienda", a.id, "modifica", {"referente_aggiunto": nome})
        db.commit()
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)


@router.post("/clienti/{azienda_id}/referenti/{referente_id}/principale", dependencies=CSRF)
def referente_principale(azienda_id: int, referente_id: int, db: Db, _: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    for r in a.referenti:
        r.principale = r.id == referente_id
    db.commit()
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)


@router.post("/clienti/{azienda_id}/referenti/{referente_id}/elimina", dependencies=CSRF)
def elimina_referente(azienda_id: int, referente_id: int, db: Db, utente: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    ref = next((r for r in a.referenti if r.id == referente_id), None)
    if ref is not None:
        a.referenti.remove(ref)
        audit.registra(
            db, utente, "azienda", a.id, "modifica", {"referente_eliminato": ref.nome_completo}
        )
        db.commit()
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)
