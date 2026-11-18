"""Pagine dei bandi (lista filtrabile e scheda) e delle fonti (stato della raccolta)."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.config import get_settings
from app.db import get_db
from app.jobs import attivita
from app.jobs.esecuzione import esegui_job
from app.models import Bando, BandoRequisiti, BandoVersione, Fonte, FonteRun, JobRun, Utente
from app.sources.incentivi_italia import ATTRIBUZIONE
from app.utils.territorio import REGIONI
from app.web import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]

PER_PAGINA = 25
STATI_FILTRO = {
    "attivi": ("aperto", "in_arrivo"),
    "aperto": ("aperto",),
    "in_arrivo": ("in_arrivo",),
    "chiuso": ("chiuso",),
    "sconosciuto": ("sconosciuto",),
    "tutti": None,
}
SCADENZE = (7, 30, 60, 90)


def _intero(valore: str | None, predefinito: int | None = None) -> int | None:
    try:
        return int(valore) if valore else predefinito
    except ValueError:
        return predefinito


def forme_disponibili(db: Session) -> list[str]:
    forme = db.scalars(select(func.unnest(BandoRequisiti.forma_agevolazione)).distinct())
    return sorted(f for f in forme if f)


@router.get("/bandi")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    p = request.query_params
    q = (p.get("q") or "").strip()
    stato = p.get("stato") or "attivi"
    regione = p.get("regione") or ""
    forma = p.get("forma") or ""
    scadenza = _intero(p.get("scadenza"))
    pagina = max(_intero(p.get("pagina"), 1) or 1, 1)

    query = select(Bando).outerjoin(BandoRequisiti)
    stati = STATI_FILTRO.get(stato, STATI_FILTRO["attivi"])
    if stati:
        query = query.where(Bando.stato.in_(stati))
    if q:
        modello = f"%{q}%"
        query = query.where(
            or_(
                Bando.titolo.ilike(modello),
                Bando.ente.ilike(modello),
                Bando.descrizione.ilike(modello),
            )
        )
    if regione == "nazionali":
        query = query.where(BandoRequisiti.territorio == "nazionale")
    elif regione in REGIONI:
        # Un'azienda di quella regione può partecipare ai bandi regionali e a quelli nazionali
        query = query.where(
            or_(
                BandoRequisiti.territorio == "nazionale",
                BandoRequisiti.regioni.any(regione),
            )
        )
    if forma:
        query = query.where(BandoRequisiti.forma_agevolazione.any(forma))
    if scadenza:
        oggi = date.today()
        query = query.where(Bando.data_chiusura.between(oggi, oggi + timedelta(days=scadenza)))

    totale = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    bandi = db.scalars(
        query.options(selectinload(Bando.requisiti))
        .order_by(Bando.data_chiusura.asc().nulls_last(), Bando.titolo)
        .limit(PER_PAGINA)
        .offset((pagina - 1) * PER_PAGINA)
    ).all()
    pagine = max((totale + PER_PAGINA - 1) // PER_PAGINA, 1)

    filtri = {"q": q, "stato": stato, "regione": regione, "forma": forma, "scadenza": scadenza}
    return render(
        request,
        "bandi/lista.html",
        {
            "bandi": bandi,
            "totale": totale,
            "pagina": pagina,
            "pagine": pagine,
            "filtri": filtri,
            "regioni": REGIONI,
            "forme": forme_disponibili(db),
            "scadenze": SCADENZE,
            "oggi": date.today(),
        },
    )


@router.get("/bandi/{bando_id}")
def scheda(request: Request, db: Db, _: Autenticato, bando_id: int) -> Response:
    bando = db.get(Bando, bando_id, options=[selectinload(Bando.requisiti)])
    if bando is None:
        raise HTTPException(status_code=404)
    versioni = db.scalar(
        select(func.count()).select_from(BandoVersione).where(BandoVersione.bando_id == bando.id)
    )
    return render(
        request,
        "bandi/scheda.html",
        {
            "bando": bando,
            "req": bando.requisiti,
            "versioni": versioni,
            "attribuzione": ATTRIBUZIONE,
            "oggi": date.today(),
        },
    )


@router.get("/fonti")
def fonti(request: Request, db: Db, _: Autenticato) -> Response:
    elenco = db.scalars(select(Fonte).order_by(Fonte.nome)).all()
    runs = db.scalars(
        select(FonteRun)
        .options(selectinload(FonteRun.fonte))
        .order_by(FonteRun.id.desc())
        .limit(20)
    ).all()
    jobs = db.scalars(
        select(JobRun).where(JobRun.job == "raccolta_bandi").order_by(JobRun.id.desc()).limit(10)
    ).all()
    conteggi = dict(db.execute(select(Bando.stato, func.count()).group_by(Bando.stato)).all())
    in_corso = any(j.esito == "in_corso" for j in jobs[:1])
    return render(
        request,
        "bandi/fonti.html",
        {
            "fonti": elenco,
            "runs": runs,
            "jobs": jobs,
            "conteggi": conteggi,
            "orario": get_settings().raccolta_orario,
            "in_corso": in_corso,
            "avviata": request.query_params.get("avviata") == "1",
        },
    )


@router.post("/fonti/raccogli", dependencies=[Depends(verifica_csrf)])
def raccogli_ora(_: Titolare, background: BackgroundTasks) -> Response:
    """Avvia subito la raccolta in background. Se il worker la sta già eseguendo,
    il lock fa saltare questa esecuzione (registrata come "saltato")."""
    background.add_task(esegui_job, "raccolta_bandi", attivita.raccolta_bandi)
    return RedirectResponse("/fonti?avviata=1", status_code=303)
