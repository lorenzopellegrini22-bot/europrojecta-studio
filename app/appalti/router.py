"""Pagina Appalti: ricerca dei bandi di gara pubblici per testo, categoria (natura e CPV),
territorio (regione, provincia, comune della stazione appaltante, tutta Italia), importo e
scadenza."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.appalti import anac
from app.appalti.riassunto import ErroreRiassunto, disponibile, genera
from app.appalti.scheda import CATEGORIE_SOA, FONTI, prevalente, righe_scheda, testo_scheda
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
from app.utils.ricerca import condizione, pertinenza, separa_regione
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
        "soa": (p.get("soa") or "").upper(),
        "fonte": p.get("fonte") or "",
        "nazionali": p.get("nazionali") or ("1" if "regione" not in p else ""),
        "ordine": p.get("ordine") or ("pertinenza" if (p.get("q") or "").strip() else "scadenza"),
    }
    try:
        pagina = max(int(p.get("pagina") or 1), 1)
    except ValueError:
        pagina = 1
    # Aperti: scadenza futura; le abilitazioni Consip senza termine sono sempre aperte
    query = select(Appalto).where(
        or_(
            Appalto.scadenza >= oggi,
            and_(Appalto.scadenza.is_(None), Appalto.fonte == "consip"),
        )
    )
    # Ogni parola in almeno un campo, accenti e plurali ignorati (app/utils/ricerca.py)
    regione_nel_testo, q_testo = separa_regione(filtri["q"])
    if regione_nel_testo and not filtri["regione"]:
        filtri["regione"] = regione_nel_testo
    testo = condizione(
        q_testo,
        Appalto.titolo,
        Appalto.descrizione,
        Appalto.stazione_appaltante,
        Appalto.cig,
        Appalto.id_esterno,
    )
    if testo is not None:
        query = query.where(testo)
    if filtri["soa"] in CATEGORIE_SOA:
        query = query.where(Appalto.codici_soa.any(filtri["soa"]))
    if filtri["fonte"] in FONTI:
        query = query.where(Appalto.fonte == filtri["fonte"])
    if filtri["natura"] in NATURE:
        query = query.where(Appalto.natura.any(filtri["natura"]))
    if filtri["cpv"] in DIVISIONI_CPV:
        query = query.where(Appalto.divisioni_cpv.any(filtri["cpv"]))
    # Con "anche nazionali" un filtro territoriale include le gare valide in tutta Italia
    nazionali = Appalto.regioni.is_(None) if filtri["nazionali"] else None
    if filtri["regione"] in REGIONI:
        cond = Appalto.regioni.any(filtri["regione"])
        query = query.where(or_(cond, nazionali) if nazionali is not None else cond)
    if filtri["provincia"] in PROVINCE:
        cond = Appalto.province.any(filtri["provincia"])
        query = query.where(or_(cond, nazionali) if nazionali is not None else cond)
    if filtri["comune"]:
        m = f"%{filtri['comune']}%"
        query = query.where(or_(Appalto.citta.ilike(m), Appalto.luogo_esecuzione.ilike(m)))
    minimo, massimo = _importo(filtri["importo_min"]), _importo(filtri["importo_max"])
    if minimo is not None:
        query = query.where(Appalto.importo_stimato >= minimo)
    if massimo is not None:
        query = query.where(Appalto.importo_stimato <= massimo)
    if filtri["scadenza"].isdigit():
        query = query.where(Appalto.scadenza <= oggi + timedelta(days=int(filtri["scadenza"])))
    totale = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    punti = pertinenza(q_testo, Appalto.titolo)
    if filtri["ordine"] == "pertinenza" and punti is not None:
        query = query.order_by(punti.desc(), Appalto.scadenza, Appalto.id)
    elif filtri["ordine"] == "recenti":
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
    filtri_url = urlencode({k: v for k, v in filtri.items() if v})
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
            "categorie_soa": CATEGORIE_SOA,
            "fonti": FONTI,
            "prevalente": prevalente,
        },
    )


@router.post("/appalti/aggiorna", dependencies=[Depends(verifica_csrf)])
def aggiorna(background: BackgroundTasks, _: Titolare) -> Response:
    background.add_task(esegui_job, "raccolta_appalti", attivita.raccolta_appalti)
    return RedirectResponse("/appalti?aggiornamento=1", status_code=303)


# --- Import dei file ANAC ----------------------------------------------------------------


@router.get("/appalti/importa")
def pagina_importa(request: Request, _: Titolare) -> Response:
    return render(
        request,
        "appalti/importa.html",
        {
            "esiti": request.query_params.getlist("esito"),
            "errore": request.query_params.get("errore"),
        },
    )


@router.post("/appalti/importa", dependencies=[Depends(verifica_csrf)])
def importa(
    db: Db,
    utente: Titolare,
    file: Annotated[list[UploadFile], File()],
) -> Response:
    """Uno o più file ANAC (CSV o ZIP). Prima i CIG, poi le categorie: l'ordine dei file
    caricati insieme si sistema da solo (le categorie si agganciano ai CIG già presenti)."""
    from app import audit

    ordinati = sorted(file, key=lambda f: "categor" in (f.filename or "").lower())
    esiti = []
    for f in ordinati:
        nome = (f.filename or "file").rsplit("/", 1)[-1]
        try:
            esito = anac.importa(db, f.file, nome)
        except anac.ErroreImport as e:
            db.rollback()
            return RedirectResponse(
                f"/appalti/importa?errore={quote(f'{nome}: {e}')}", status_code=303
            )
        esiti.append(f"{nome} – {esito.riepilogo()}")
        audit.registra(db, utente, "appalto", None, "crea", {"import_anac": nome})
        db.commit()
    return RedirectResponse(
        "/appalti/importa?" + urlencode([("esito", e) for e in esiti]), status_code=303
    )


# --- Scheda e riassunto ------------------------------------------------------------------


def _appalto(db: Session, appalto_id: int) -> Appalto:
    a = db.get(Appalto, appalto_id)
    if a is None:
        raise HTTPException(404)
    return a


@router.get("/appalti/{appalto_id}")
def scheda(request: Request, appalto_id: int, db: Db, _: Autenticato) -> Response:
    a = _appalto(db, appalto_id)
    ai_ok, ai_motivo = disponibile(db)
    return render(
        request,
        "appalti/scheda.html",
        {
            "a": a,
            "righe": righe_scheda(a),
            "testo": testo_scheda(a),
            "ai_ok": ai_ok,
            "ai_motivo": ai_motivo,
            "errore": request.query_params.get("errore"),
            "oggi": date.today(),
        },
    )


@router.post("/appalti/{appalto_id}/riassunto", dependencies=[Depends(verifica_csrf)])
def riassumi(appalto_id: int, db: Db, _: Autenticato) -> Response:
    a = _appalto(db, appalto_id)
    try:
        genera(db, a)
    except ErroreRiassunto as e:
        return RedirectResponse(
            f"/appalti/{a.id}?errore={quote(str(e))}#riassunto", status_code=303
        )
    return RedirectResponse(f"/appalti/{a.id}#riassunto", status_code=303)


@router.post("/appalti/{appalto_id}/documenti", dependencies=[Depends(verifica_csrf)])
def cerca_documenti(appalto_id: int, db: Db, _: Autenticato) -> Response:
    """Legge subito la documentazione di questa gara (di notte lo fa il worker per tutte)."""
    from app.appalti.documenti import leggi_gara
    from app.documenti.servizio import Robots
    from app.sources import http

    a = _appalto(db, appalto_id)
    with http.client() as client:
        esito = leggi_gara(a, client, Robots(client))
    db.commit()
    return RedirectResponse(f"/appalti/{a.id}?msg={quote('Documentazione: ' + esito)}", 303)
