"""Bandi inseriti a mano dallo studio: le misure che le fonti automatiche non hanno.

Il 20/11/2026 il file open data di Incentivi Italia non conteneva Resto al Sud 2.0,
Transizione 5.0 e Autoimpiego Centro-Nord. Il consulente li inserisce qui: finiscono nella
fonte «manuale», che la raccolta notturna non tocca (nessun file da scaricare, nessuna
chiusura per «scomparsa»); lo stato si ricalcola ogni notte dalle date. Per il resto sono
bandi come gli altri: ricerca, filtri, opportunità per i clienti, AI, PDF, Segui, pratiche.
I requisiti scritti nel modulo valgono come dati «della fonte» (prevalgono sull'AI).
"""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.bandi.stato import calcola_stato
from app.db import get_db
from app.models import Bando, BandoRequisiti, Fonte, Utente
from app.sources.base import RequisitiFonte
from app.utils.numeri import importo_italiano
from app.utils.territorio import REGIONI
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

CODICE_FONTE = "manuale"
NOME_FONTE = "Inseriti dallo studio"
DIMENSIONI = (("micro", "Micro"), ("piccola", "Piccola"), ("media", "Media"), ("grande", "Grande"))
STATI = (("aperto", "Aperto"), ("in_arrivo", "In arrivo"), ("chiuso", "Chiuso"))
# forme usate da Incentivi Italia: le stesse parole tengono insieme filtri e abbinamento
FORME_BASE = (
    "Contributo/Fondo perduto",
    "Finanziamento agevolato",
    "Agevolazione fiscale",
    "Garanzia",
    "Prestito/Anticipo rimborsabile",
    "Partecipazione al capitale",
)
_ATECO = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")


def fonte_manuale(db: Session) -> Fonte:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == CODICE_FONTE))
    if fonte is None:
        fonte = Fonte(codice=CODICE_FONTE, nome=NOME_FONTE, url="/bandi/nuovo")
        db.add(fonte)
        db.flush()
    return fonte


def e_manuale(db: Session, b: Bando) -> bool:
    return db.scalar(select(Fonte.codice).where(Fonte.id == b.fonte_id)) == CODICE_FONTE


def _lista(form: dict[str, Any], campo: str) -> list[str]:
    v = form.get(campo)
    voci = v if isinstance(v, list) else [v] if v else []
    return [x.strip() for x in voci if isinstance(x, str) and x.strip()]


def _data(form: dict[str, Any], campo: str) -> date | None:
    t = testo(form, campo)
    try:
        return date.fromisoformat(t) if t else None
    except ValueError:
        return None


def _importo(form: dict[str, Any], campo: str) -> Decimal | None:
    t = testo(form, campo)
    if not t:
        return None
    try:
        v = importo_italiano(t)
    except (ValueError, ArithmeticError):
        return None
    return v if v and v > 0 else None


def leggi(form: dict[str, Any]) -> tuple[dict[str, Any], RequisitiFonte, list[str]]:
    """Campi del bando, requisiti ed eventuali errori dal modulo."""
    errori: list[str] = []
    titolo = testo(form, "titolo")
    if not titolo:
        errori.append("Il titolo è obbligatorio.")
    apertura, chiusura = _data(form, "data_apertura"), _data(form, "data_chiusura")
    if apertura and chiusura and chiusura < apertura:
        errori.append("La chiusura è prima dell'apertura.")
    for campo in ("link_ufficiale", "link_domande"):
        link = testo(form, campo)
        if link and not link.startswith(("https://", "http://")):
            errori.append("I link devono iniziare con https:// (o http://).")
            break

    regioni = [r for r in _lista(form, "regioni") if r in REGIONI]
    comuni = [c.strip() for c in (testo(form, "comuni") or "").splitlines() if c.strip()]
    ambito = testo(form, "ambito") or "nazionale"
    if ambito == "regionale" and not regioni:
        errori.append("Per un bando regionale scegli almeno una regione.")
    if ambito == "nazionale":
        territorio, regioni, comuni = "nazionale", [], []
    elif comuni:
        territorio = "comunale"
    else:
        territorio = "regionale"

    ateco = [
        c.strip().rstrip(".") for c in re.split(r"[;,\s]+", testo(form, "ateco") or "") if c.strip()
    ]
    if any(not _ATECO.match(c) for c in ateco):
        errori.append("Codici ATECO: scrivili come 25.62 o 25.62.00, separati da punto e virgola.")
        ateco = [c for c in ateco if _ATECO.match(c)]

    dimensioni = [d for d in _lista(form, "dimensioni") if d in dict(DIMENSIONI)]
    forme = _lista(form, "forme")
    altra = testo(form, "forma_altra")
    if altra:
        forme.append(altra)
    da_verificare = [] if dimensioni else ["dimensioni"]
    requisiti = RequisitiFonte(
        territorio=territorio,
        regioni=regioni or None,
        comuni=comuni or None,
        dimensioni=dimensioni or None,
        ateco_ammessi=ateco or None,
        ateco_versione=(testo(form, "ateco_versione") or "2007") if ateco else None,
        forma_agevolazione=forme or None,
        importo_max=_importo(form, "importo_max"),
        dotazione=_importo(form, "dotazione"),
        da_verificare=da_verificare,
    )
    stato_dichiarato = testo(form, "stato_dichiarato")
    campi = {
        "titolo": titolo or "",
        "ente": testo(form, "ente"),
        "descrizione": testo(form, "descrizione"),
        "link_ufficiale": testo(form, "link_ufficiale"),
        "link_domande": testo(form, "link_domande"),
        "data_apertura": apertura,
        "data_chiusura": chiusura,
        "note_apertura_chiusura": testo(form, "note"),
        "stato_fonte": stato_dichiarato if stato_dichiarato in dict(STATI) else None,
    }
    return campi, requisiti, errori


def _applica(b: Bando, campi: dict[str, Any], requisiti: RequisitiFonte, oggi: date) -> None:
    from app.sources.runner import scrivi_requisiti

    for k, v in campi.items():
        setattr(b, k, v)
    b.raw = {"inserito_a_mano": True, **{k: str(v) for k, v in campi.items() if v is not None}}
    b.hash_contenuto = uuid.uuid4().hex + uuid.uuid4().hex  # un bando modificato è «cambiato»
    b.stato = calcola_stato(b.data_apertura, b.data_chiusura, oggi, stato_fonte=b.stato_fonte)
    scrivi_requisiti(b, requisiti)


def _valori_modulo(b: Bando | None) -> dict[str, Any]:
    """Valori per ricompilare il modulo (nuovo bando: vuoto)."""
    if b is None:
        return {"ambito": "nazionale", "regioni": [], "dimensioni": [], "forme": []}
    r: BandoRequisiti | None = b.requisiti
    fonte = (r.valori_fonte if r else None) or {}
    return {
        "titolo": b.titolo,
        "ente": b.ente,
        "descrizione": b.descrizione,
        "link_ufficiale": b.link_ufficiale,
        "link_domande": b.link_domande,
        "data_apertura": b.data_apertura.isoformat() if b.data_apertura else "",
        "data_chiusura": b.data_chiusura.isoformat() if b.data_chiusura else "",
        "note": b.note_apertura_chiusura,
        "stato_dichiarato": b.stato_fonte or "",
        "ambito": "nazionale" if fonte.get("territorio") == "nazionale" else "regionale",
        "regioni": fonte.get("regioni") or [],
        "comuni": "\n".join(fonte.get("comuni") or []),
        "dimensioni": fonte.get("dimensioni") or [],
        "ateco": "; ".join(fonte.get("ateco_ammessi") or []),
        "ateco_versione": fonte.get("ateco_versione") or "2007",
        "forme": fonte.get("forma_agevolazione") or [],
        "importo_max": fonte.get("importo_max") or "",
        "dotazione": fonte.get("dotazione") or "",
    }


def _modulo(
    request: Request, db: Session, valori: dict[str, Any], bando: Bando | None, errori: list[str]
) -> Response:
    from app.bandi.router import forme_disponibili

    forme = list(dict.fromkeys([*FORME_BASE, *forme_disponibili(db)]))
    return render(
        request,
        "bandi/modulo.html",
        {
            "v": valori,
            "bando": bando,
            "errori": errori,
            "regioni": REGIONI,
            "dimensioni": DIMENSIONI,
            "stati": STATI,
            "forme": forme,
        },
        400 if errori else 200,
    )


def _dal_form(form: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: form.get(k) for k in form},
        "regioni": _lista(form, "regioni"),
        "dimensioni": _lista(form, "dimensioni"),
        "forme": _lista(form, "forme"),
    }


@router.get("/bandi/nuovo")
def nuovo(request: Request, db: Db, _: Autenticato) -> Response:
    return _modulo(request, db, _valori_modulo(None), None, [])


@router.post("/bandi/nuovo", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    campi, requisiti, errori = leggi(form)
    if errori:
        return _modulo(request, db, _dal_form(form), None, errori)
    fonte = fonte_manuale(db)
    b = Bando(
        fonte_id=fonte.id,
        id_esterno=f"manuale-{uuid.uuid4().hex[:16]}",
        visto_la_prima_volta=datetime.now(UTC),
    )
    _applica(b, campi, requisiti, date.today())
    db.add(b)
    db.flush()
    audit.registra(db, utente, "bando", b.id, "crea", {"titolo": b.titolo, "a_mano": True})
    db.commit()
    return RedirectResponse(f"/bandi/{b.id}?inserito=1", status_code=303)


def _bando_manuale(db: Session, bando_id: int) -> Bando:
    b = db.get(Bando, bando_id)
    if b is None or not e_manuale(db, b):
        raise HTTPException(404)
    return b


@router.get("/bandi/{bando_id}/modifica")
def modifica(request: Request, bando_id: int, db: Db, _: Autenticato) -> Response:
    b = _bando_manuale(db, bando_id)
    return _modulo(request, db, _valori_modulo(b), b, [])


@router.post("/bandi/{bando_id}/modifica", dependencies=CSRF)
def salva(request: Request, bando_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    b = _bando_manuale(db, bando_id)
    campi, requisiti, errori = leggi(form)
    if errori:
        return _modulo(request, db, _dal_form(form), b, errori)
    _applica(b, campi, requisiti, date.today())
    audit.registra(db, utente, "bando", b.id, "modifica", {"titolo": b.titolo})
    db.commit()
    return RedirectResponse(f"/bandi/{b.id}", status_code=303)


@router.post("/bandi/{bando_id}/elimina", dependencies=CSRF)
def elimina(bando_id: int, db: Db, utente: Autenticato) -> Response:
    from app.cestino import servizio as cestino

    b = _bando_manuale(db, bando_id)
    audit.registra(db, utente, "bando", b.id, "elimina", {"titolo": b.titolo})
    e = cestino.sposta(db, utente, b, "bando", b.titolo, f"/bandi/{b.id}")
    db.delete(b)
    db.commit()
    return RedirectResponse(f"/bandi?cestino={e.id}", status_code=303)


def aggiorna_stati(db: Session, oggi: date | None = None) -> int:
    """Ogni notte: lo stato dei bandi inseriti a mano segue le loro date."""
    fonte = db.scalar(select(Fonte).where(Fonte.codice == CODICE_FONTE))
    if fonte is None:
        return 0
    cambiati = 0
    for b in db.scalars(select(Bando).where(Bando.fonte_id == fonte.id)):
        nuovo = calcola_stato(
            b.data_apertura, b.data_chiusura, oggi or date.today(), stato_fonte=b.stato_fonte
        )
        if nuovo != b.stato:
            b.stato = nuovo
            cambiati += 1
    return cambiati


def conta(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Bando)
            .join(Fonte, Fonte.id == Bando.fonte_id)
            .where(Fonte.codice == CODICE_FONTE)
        )
        or 0
    )
