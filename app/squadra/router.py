"""Pagine e azioni per l'uso quotidiano: «Oggi», ricerca unica, calendario, note, «Segui»."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.auth.permessi import puo_modificare, puo_vedere
from app.db import get_db
from app.models import Appalto, Bando, Commento, Opportunita, Pratica, Utente
from app.squadra import calendario, note, ricerca, seguiti
from app.web import leggi_form, render, testo

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
CSRF = [Depends(verifica_csrf)]


# --- Oggi ----------------------------------------------------------------------------------


def _contesto_oggi(db: Session, utente: Utente) -> dict:
    from app.models.pratiche import STATI_PRATICA_CONCLUSI
    from app.pratiche.servizio import scadenze

    oggi = date.today()
    ctx: dict = {"oggi": oggi, "citazione": note.citazione(utente)}
    if puo_vedere(utente, "/pratiche"):
        # il titolare vede anche i promemoria dei clienti, che non hanno un responsabile
        voci = scadenze(
            db,
            oggi - timedelta(days=60),
            oggi + timedelta(days=7),
            utente.id,
            promemoria=utente.is_titolare,
        )
        ctx["in_ritardo"] = [v for v in voci if v.data < oggi]
        ctx["settimana"] = [v for v in voci if v.data >= oggi]
        ctx["mie_pratiche"] = list(
            db.scalars(
                select(Pratica)
                .where(
                    Pratica.responsabile_id == utente.id,
                    Pratica.stato.not_in(STATI_PRATICA_CONCLUSI),
                )
                .options(selectinload(Pratica.azienda), selectinload(Pratica.bando))
                .order_by(Pratica.scadenza_presentazione.asc().nulls_last(), Pratica.id)
                .limit(30)
            )
        )
    if puo_vedere(utente, "/opportunita"):
        ctx["opportunita"] = list(
            db.scalars(
                select(Opportunita)
                .where(Opportunita.stato == "nuova")
                .options(
                    selectinload(Opportunita.azienda),
                    selectinload(Opportunita.bando),
                    selectinload(Opportunita.appalto),
                )
                .order_by(Opportunita.punteggio.desc(), Opportunita.id.desc())
                .limit(10)
            )
        )
    ctx["menzioni"] = note.menzioni_da_leggere(db, utente)
    ctx["novita"] = seguiti.novita(db, utente)
    ctx["calendario_attivo"] = utente.calendario_hash is not None
    return ctx


@router.get("/oggi")
def oggi(request: Request, db: Db, utente: Autenticato) -> Response:
    return render(request, "squadra/oggi.html", _contesto_oggi(db, utente))


@router.post("/oggi/calendario", dependencies=CSRF)
def crea_calendario(request: Request, db: Db, utente: Autenticato) -> Response:
    """Crea (o rigenera) il link al calendario personale: compare una volta sola."""
    nuovo = calendario.crea_link(utente)
    audit.registra(db, utente, "utente", utente.id, "modifica", {"calendario": "link creato"})
    db.commit()
    ctx = _contesto_oggi(db, utente)
    ctx["calendario_link"] = nuovo
    return render(request, "squadra/oggi.html", ctx)


@router.post("/oggi/calendario/disattiva", dependencies=CSRF)
def disattiva_calendario(db: Db, utente: Autenticato) -> Response:
    utente.calendario_hash = None
    audit.registra(db, utente, "utente", utente.id, "modifica", {"calendario": "link disattivato"})
    db.commit()
    return RedirectResponse("/oggi#calendario", 303)


@router.post("/oggi/letto", dependencies=CSRF)
async def segna_tutto_letto(request: Request, db: Db, utente: Autenticato) -> Response:
    from datetime import UTC, datetime

    from app.models import Menzione, NovitaSeguito, Seguito

    form = await leggi_form(request)
    ora = datetime.now(UTC)
    if testo(form, "cosa") == "menzioni":
        for m in db.scalars(
            select(Menzione).where(Menzione.utente_id == utente.id, Menzione.letta_il.is_(None))
        ):
            m.letta_il = ora
    else:
        for n in db.scalars(
            select(NovitaSeguito)
            .join(Seguito)
            .where(Seguito.utente_id == utente.id, NovitaSeguito.letta_il.is_(None))
        ):
            n.letta_il = ora
    db.commit()
    return RedirectResponse("/oggi", 303)


# --- Calendario (pubblico: protetto dal token nel link) -----------------------------------


@router.get("/calendario/{nome}")
def file_calendario(nome: str, db: Db) -> Response:
    if not nome.endswith(".ics"):
        raise HTTPException(404)
    u = calendario.utente_dal_token(db, nome.removesuffix(".ics"))
    if u is None:
        raise HTTPException(404)
    return Response(
        calendario.genera(db, u),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=900", "X-Robots-Tag": "noindex"},
    )


# --- Ricerca unica -------------------------------------------------------------------------


@router.get("/cerca")
def cerca(request: Request, db: Db, utente: Autenticato) -> Response:
    q = (request.query_params.get("q") or "").strip()
    gruppi = ricerca.cerca(db, utente, q)
    # un solo risultato: si apre direttamente
    trovati = [v for g in gruppi for v in g.voci]
    if len(trovati) == 1 and request.query_params.get("vai") != "0":
        return RedirectResponse(trovati[0]["url"], 303)
    return render(request, "squadra/cerca.html", {"q": q, "gruppi": gruppi, "totale": len(trovati)})


# --- Note con @menzioni ----------------------------------------------------------------------


def _oggetto(db: Session, oggetto: str, oggetto_id: int) -> Pratica | Opportunita:
    modello = Pratica if oggetto == "pratica" else Opportunita
    x = db.get(modello, oggetto_id)
    if x is None:
        raise HTTPException(404)
    return x


async def _aggiungi_nota(
    request: Request, db: Session, utente: Utente, oggetto: str, oid: int
) -> Response:
    _oggetto(db, oggetto, oid)
    form = await leggi_form(request)
    corpo = testo(form, "testo") or ""
    destinazione = note.link(oggetto, oid)
    if corpo.strip():
        c = note.aggiungi(db, oggetto, oid, corpo, utente, destinazione)
        audit.registra(db, utente, oggetto, oid, "modifica", {"nota": c.id})
        db.commit()
    return RedirectResponse(destinazione, 303)


@router.post("/pratiche/{pid}/note", dependencies=CSRF)
async def nota_pratica(request: Request, pid: int, db: Db, utente: Autenticato) -> Response:
    return await _aggiungi_nota(request, db, utente, "pratica", pid)


@router.post("/opportunita/{oid}/note", dependencies=CSRF)
async def nota_opportunita(request: Request, oid: int, db: Db, utente: Autenticato) -> Response:
    return await _aggiungi_nota(request, db, utente, "opportunita", oid)


def _elimina_nota(db: Session, utente: Utente, oggetto: str, oid: int, cid: int) -> Response:
    c = db.get(Commento, cid)
    if c is None or c.oggetto != oggetto or c.oggetto_id != oid:
        raise HTTPException(404)
    if c.utente_id != utente.id and not utente.is_titolare:
        raise HTTPException(403)
    db.delete(c)
    audit.registra(db, utente, oggetto, oid, "modifica", {"nota_eliminata": cid})
    db.commit()
    return RedirectResponse(note.link(oggetto, oid), 303)


@router.post("/pratiche/{pid}/note/{cid}/elimina", dependencies=CSRF)
def elimina_nota_pratica(pid: int, cid: int, db: Db, utente: Autenticato) -> Response:
    return _elimina_nota(db, utente, "pratica", pid, cid)


@router.post("/opportunita/{oid}/note/{cid}/elimina", dependencies=CSRF)
def elimina_nota_opportunita(oid: int, cid: int, db: Db, utente: Autenticato) -> Response:
    return _elimina_nota(db, utente, "opportunita", oid, cid)


@router.get("/opportunita/{oid}")
def scheda_opportunita(request: Request, oid: int, db: Db, utente: Autenticato) -> Response:
    from app.documenti.documentazione import voci_bando, voci_gara
    from app.models import DocumentoBando
    from app.opportunita.router import ETICHETTE_STATO

    o = db.get(
        Opportunita,
        oid,
        options=[
            selectinload(Opportunita.azienda),
            selectinload(Opportunita.bando),
            selectinload(Opportunita.appalto),
        ],
    )
    if o is None:
        raise HTTPException(404)
    if o.bando:
        pdf = list(db.scalars(select(DocumentoBando).where(DocumentoBando.bando_id == o.bando_id)))
        documenti = voci_bando(o.bando, pdf)
    else:
        documenti = voci_gara(o.appalto) if o.appalto else []
    note.segna_lette(db, utente, "opportunita", o.id)
    db.commit()
    return render(
        request,
        "squadra/opportunita.html",
        {
            "o": o,
            "documenti": documenti,
            "note": note.elenco(db, "opportunita", o.id),
            "etichette": ETICHETTE_STATO,
            "citazioni": _citazioni(db, utente, "opportunita"),
        },
    )


def _citazioni(db: Session, utente: Utente, oggetto: str) -> list[str]:
    return [note.citazione(u) for u in note.citabili(db, oggetto, utente)]


def contesto_note(db: Session, utente: Utente, oggetto: str, oid: int) -> dict:
    """Per la scheda pratica: note, chi si può citare; le menzioni dell'utente diventano lette."""
    note.segna_lette(db, utente, oggetto, oid)
    db.commit()
    return {"note": note.elenco(db, oggetto, oid), "citazioni": _citazioni(db, utente, oggetto)}


# --- Segui bandi e gare ------------------------------------------------------------------------


@router.post("/seguiti/{tipo}/{oid}", dependencies=CSRF)
async def segui(request: Request, tipo: str, oid: int, db: Db, utente: Autenticato) -> Response:
    if tipo not in ("bando", "gara"):
        raise HTTPException(404)
    pagina = f"/bandi/{oid}" if tipo == "bando" else f"/appalti/{oid}"
    if not puo_vedere(utente, pagina):
        raise HTTPException(403)
    form = await leggi_form(request)
    bando = db.get(Bando, oid) if tipo == "bando" else None
    gara = db.get(Appalto, oid) if tipo == "gara" else None
    if bando is None and gara is None:
        raise HTTPException(404)
    if testo(form, "azione") == "smetti":
        seguiti.smetti(db, utente, bando.id if bando else None, gara.id if gara else None)
        db.commit()
    else:
        seguiti.segui(db, utente, bando, gara)
        try:
            db.commit()
        except IntegrityError:  # doppio clic: lo segue già
            db.rollback()
    return RedirectResponse(pagina, 303)


def contesto_seguito(
    db: Session, utente: Utente, bando_id: int | None = None, appalto_id: int | None = None
) -> dict:
    """Per le schede di bando e gara: se l'utente segue e le sue novità (poi lette)."""
    elenco = [
        n for n, _t, _l in seguiti.novita(db, utente, bando_id=bando_id, appalto_id=appalto_id)
    ]
    seguiti.segna_lette(db, utente, bando_id, appalto_id)
    db.commit()
    return {
        "segue": seguiti.segue(db, utente, bando_id, appalto_id),
        "novita_qui": elenco,
        "puo_scrivere": puo_modificare(utente, "/seguiti", "POST"),
    }


# --- App sul telefono (manifest per «Aggiungi a schermata Home») ------------------------------


@router.get("/manifest.webmanifest")
def manifest() -> Response:
    import json

    from app import NOME_APP

    dati = {
        "name": NOME_APP,
        "short_name": "Europrojecta",
        "description": "Bandi, gare, clienti e pratiche dello studio",
        "lang": "it",
        "start_url": "/oggi",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#0544d4",
        "icons": [
            {"src": "/static/img/icona-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/icona-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "/static/img/icona-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {"name": "Oggi", "url": "/oggi"},
            {"name": "Scadenzario", "url": "/scadenzario"},
            {"name": "Cerca", "url": "/cerca"},
        ],
    }
    return Response(
        json.dumps(dati, ensure_ascii=False),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )
