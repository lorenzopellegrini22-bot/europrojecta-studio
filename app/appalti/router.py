"""Pagina Appalti: ricerca dei bandi di gara pubblici per testo, categoria (natura e CPV),
territorio (regione, provincia, comune della stazione appaltante, tutta Italia), importo e
scadenza."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.appalti.ted import ATTRIBUZIONE
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.jobs import attivita
from app.jobs.esecuzione import esegui_job
from app.models import JobRun, Utente
from app.models.appalti import Appalto
from app.utils.cpv import DIVISIONI_CPV
from app.utils.numeri import importo_italiano
from app.utils.nuts_italia import NUTS3_PROVINCIA
from app.utils.territorio import REGIONI
from app.web import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]

PER_PAGINA = 25
NATURE = {"lavori": "Lavori", "servizi": "Servizi", "forniture": "Forniture"}
FONTI_ESTERNE = (
    (
        "Banca Dati Nazionale dei Contratti Pubblici (ANAC)",
        "https://dati.anticorruzione.it/superset/dashboard/appalti/",
        "Tutti gli appalti, anche sotto soglia. Dati aperti scaricabili dal portale; il portale "
        "non consente lo scarico automatico dal server.",
    ),
    (
        "Acquisti in Rete PA / MePA (Consip)",
        "https://www.acquistinretepa.it/",
        "Mercato elettronico e convenzioni: per partecipare serve l'abilitazione dell'impresa.",
    ),
    (
        "Servizio Contratti Pubblici (MIT)",
        "https://www.serviziocontrattipubblici.it/",
        "Bandi ed esiti pubblicati dalle stazioni appaltanti.",
    ),
    (
        "InfoAppalti",
        "https://www.infoappalti.it",
        "Servizio privato di segnalazione gare, in abbonamento.",
    ),
)
PROVINCE = sorted({nome for nome, _ in NUTS3_PROVINCIA.values()})


def _importo(valore: str | None) -> Decimal | None:
    return importo_italiano(valore)


@router.get("/appalti")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    p = request.query_params
    oggi = date.today()
    filtri = {
        "q": (p.get("q") or "").strip(),
        "natura": p.get("natura") or "",
        "cpv": p.get("cpv") or "",
        "regione": p.get("regione") or "",
        "provincia": p.get("provincia") or "",
        "comune": (p.get("comune") or "").strip(),
        "importo_min": p.get("importo_min") or "",
        "importo_max": p.get("importo_max") or "",
        "scadenza": p.get("scadenza") or "",
        "ordine": p.get("ordine") or "scadenza",
    }
    try:
        pagina = max(int(p.get("pagina") or 1), 1)
    except ValueError:
        pagina = 1
    query = select(Appalto).where(Appalto.scadenza >= oggi)
    if filtri["q"]:
        m = f"%{filtri['q']}%"
        query = query.where(
            or_(
                Appalto.titolo.ilike(m),
                Appalto.stazione_appaltante.ilike(m),
                Appalto.id_esterno.ilike(m),
            )
        )
    if filtri["natura"] in NATURE:
        query = query.where(Appalto.natura.any(filtri["natura"]))
    if filtri["cpv"] in DIVISIONI_CPV:
        query = query.where(Appalto.divisioni_cpv.any(filtri["cpv"]))
    if filtri["regione"] in REGIONI:
        query = query.where(Appalto.regioni.any(filtri["regione"]))
    if filtri["provincia"] in PROVINCE:
        query = query.where(Appalto.province.any(filtri["provincia"]))
    if filtri["comune"]:
        query = query.where(Appalto.citta.ilike(f"%{filtri['comune']}%"))
    minimo, massimo = _importo(filtri["importo_min"]), _importo(filtri["importo_max"])
    if minimo is not None:
        query = query.where(Appalto.importo_stimato >= minimo)
    if massimo is not None:
        query = query.where(Appalto.importo_stimato <= massimo)
    if filtri["scadenza"].isdigit():
        query = query.where(Appalto.scadenza <= oggi + timedelta(days=int(filtri["scadenza"])))
    totale = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if filtri["ordine"] == "recenti":
        query = query.order_by(Appalto.pubblicazione.desc().nulls_last(), Appalto.id.desc())
    elif filtri["ordine"] == "importo":
        query = query.order_by(Appalto.importo_stimato.desc().nulls_last(), Appalto.id)
    else:
        query = query.order_by(Appalto.scadenza, Appalto.id)
    appalti = list(db.scalars(query.offset((pagina - 1) * PER_PAGINA).limit(PER_PAGINA)))
    ultima = db.scalar(
        select(JobRun)
        .where(JobRun.job == "raccolta_appalti", JobRun.esito == "ok")
        .order_by(JobRun.inizio.desc())
        .limit(1)
    )
    in_corso = db.scalar(
        select(JobRun).where(JobRun.job == "raccolta_appalti", JobRun.esito == "in_corso").limit(1)
    )
    filtri_url = "&".join(f"{k}={v}" for k, v in filtri.items() if v)
    return render(
        request,
        "appalti/lista.html",
        {
            "appalti": appalti,
            "totale": totale,
            "pagina": pagina,
            "pagine": max((totale + PER_PAGINA - 1) // PER_PAGINA, 1),
            "filtri": filtri,
            "filtri_url": filtri_url,
            "nature": NATURE,
            "divisioni": DIVISIONI_CPV,
            "regioni": REGIONI,
            "province": PROVINCE,
            "ultima": ultima,
            "in_corso": in_corso is not None,
            "fonti_esterne": FONTI_ESTERNE,
            "attribuzione": ATTRIBUZIONE,
            "oggi": oggi,
        },
    )


@router.post("/appalti/aggiorna", dependencies=[Depends(verifica_csrf)])
def aggiorna(background: BackgroundTasks, _: Titolare) -> Response:
    background.add_task(esegui_job, "raccolta_appalti", attivita.raccolta_appalti)
    return RedirectResponse("/appalti?aggiornamento=1", status_code=303)
