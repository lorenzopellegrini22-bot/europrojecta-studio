"""Applicazione FastAPI: middleware di sicurezza, router, /health.

Gli endpoint sono sincroni (def, non async def): con un solo utente l'async non serve.
Tutte le pagine richiedono il login, tranne /login e /health (c'è un test che lo verifica).
"""

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import NOME_APP
from app.aggiornamenti.router import router as aggiornamenti_router
from app.appalti.router import router as appalti_router
from app.auth.account import router as account_router
from app.auth.dipendenze import CsrfNonValido, NonAutenticato, Proibito, utente_corrente
from app.auth.router import router as auth_router
from app.bandi.router import router as bandi_router
from app.clienti.crm import router as crm_router
from app.clienti.router import router as clienti_router
from app.clienti.segnalatori import router as segnalatori_router
from app.db import get_db
from app.documenti.router import router as documenti_router
from app.documenti.servizio import da_caricare as documenti_da_caricare
from app.esportazione import router as export_router
from app.extraction.router import router as estrazione_router
from app.extraction.servizio import stato_budget
from app.importer.router import router as import_router
from app.integrazioni import servizio as servizio_integrazioni
from app.integrazioni.router import router as integrazioni_router
from app.models import AggiornamentoCliente, Bando, Fonte, FonteRun, Opportunita, Pratica, Utente
from app.models.pratiche import STATI_PRATICA_CONCLUSI
from app.opportunita.router import router as opportunita_router
from app.pratiche.prefattibilita import router as prefattibilita_router
from app.pratiche.router import router as pratiche_router
from app.pratiche.servizio import ETICHETTE_STATO_PRATICA, scadenze
from app.pratiche.statistiche import router as statistiche_router
from app.web import render

log = logging.getLogger(__name__)

app = FastAPI(title=NOME_APP, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

METODI_CHE_MODIFICANO = {"POST", "PUT", "PATCH", "DELETE"}

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _stessa_origine(request: Request) -> bool:
    """Origin (o in mancanza Referer) deve corrispondere all'host richiesto."""
    host = request.headers.get("host", "")
    origine = request.headers.get("origin") or request.headers.get("referer")
    if not origine or origine == "null":
        return False
    return urlsplit(origine).netloc == host


@app.middleware("http")
async def sicurezza(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.method in METODI_CHE_MODIFICANO and not _stessa_origine(request):
        return Response("Richiesta da un'origine non ammessa.", status_code=403)
    risposta: Response = await call_next(request)
    risposta.headers["Content-Security-Policy"] = CSP
    risposta.headers["X-Frame-Options"] = "DENY"
    risposta.headers["X-Content-Type-Options"] = "nosniff"
    risposta.headers["Referrer-Policy"] = "same-origin"
    risposta.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith("/static"):
        risposta.headers["Cache-Control"] = "no-store"
    return risposta


@app.exception_handler(NonAutenticato)
def _non_autenticato(request: Request, _: NonAutenticato) -> Response:
    if request.headers.get("hx-request"):
        # HTMX: reindirizza l'intera pagina, non il frammento
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Proibito)
def _proibito(request: Request, _: Proibito) -> Response:
    return render(
        request, "errore.html", {"messaggio": "Non hai i permessi per questa pagina."}, 403
    )


@app.exception_handler(CsrfNonValido)
def _csrf(request: Request, _: CsrfNonValido) -> Response:
    return render(
        request,
        "errore.html",
        {"messaggio": "La pagina è scaduta. Ricaricala e riprova."},
        403,
    )


app.include_router(auth_router)
app.include_router(account_router)
app.include_router(integrazioni_router)
app.include_router(bandi_router)
app.include_router(crm_router)
app.include_router(aggiornamenti_router)
app.include_router(import_router)
app.include_router(export_router)
app.include_router(clienti_router)
app.include_router(pratiche_router)
app.include_router(segnalatori_router)
app.include_router(prefattibilita_router)
app.include_router(statistiche_router)
app.include_router(appalti_router)
app.include_router(opportunita_router)
app.include_router(estrazione_router)
app.include_router(documenti_router)


@app.get("/")
def home(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    utente: Annotated[Utente, Depends(utente_corrente)],
) -> Response:
    oggi = date.today()
    aperti = db.scalar(
        select(func.count())
        .select_from(Bando)
        .where(Bando.stato == "aperto", Bando.bando_canonico_id.is_(None))
    )
    in_arrivo = db.scalar(
        select(func.count())
        .select_from(Bando)
        .where(Bando.stato == "in_arrivo", Bando.bando_canonico_id.is_(None))
    )
    in_scadenza = db.scalar(
        select(func.count())
        .select_from(Bando)
        .where(
            Bando.stato == "aperto",
            Bando.bando_canonico_id.is_(None),
            Bando.data_chiusura.between(oggi, oggi + timedelta(7)),
        )
    )
    # "Nuovi" = comparsi dopo la prima raccolta riuscita (la prima importa tutto lo storico)
    prima_raccolta = db.scalar(select(func.min(FonteRun.fine)).where(FonteRun.esito == "ok"))
    nuovi = 0
    if prima_raccolta is not None:
        nuovi = db.scalar(
            select(func.count())
            .select_from(Bando)
            .where(
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
                Bando.visto_la_prima_volta > prima_raccolta,
                Bando.visto_la_prima_volta >= datetime.now(UTC) - timedelta(days=7),
            )
        )
    fonti_in_errore = db.scalars(select(Fonte).where(Fonte.fallimenti_consecutivi >= 2)).all()
    pratiche_attive = db.scalar(
        select(func.count())
        .select_from(Pratica)
        .where(Pratica.stato.not_in(STATI_PRATICA_CONCLUSI))
    )
    scadenze_vicine = scadenze(db, oggi - timedelta(days=30), oggi + timedelta(days=7), utente.id)
    ultima = db.scalar(select(func.max(Fonte.ultimo_successo)))
    integrazioni = (
        [servizio_integrazioni.stato(db, c) for c in ("openai", "smtp")]
        if utente.is_titolare
        else []
    )
    return render(
        request,
        "home.html",
        {
            "aperti": aperti,
            "in_arrivo": in_arrivo,
            "in_scadenza": in_scadenza,
            "nuovi": nuovi,
            "fonti_in_errore": fonti_in_errore,
            "ultima_raccolta": ultima,
            "integrazioni": integrazioni,
            "pratiche_attive": pratiche_attive,
            "pdf_da_caricare": len(documenti_da_caricare(db)),
            "opportunita_nuove": db.scalar(
                select(func.count()).select_from(Opportunita).where(Opportunita.stato == "nuova")
            ),
            "scadenze_vicine": scadenze_vicine,
            "pratiche_per_stato": db.execute(
                select(Pratica.stato, func.count())
                .where(Pratica.stato.not_in(STATI_PRATICA_CONCLUSI))
                .group_by(Pratica.stato)
            ).all(),
            "etichette_pratica": ETICHETTE_STATO_PRATICA,
            "bozze_aggiornamenti": db.scalar(
                select(func.count())
                .select_from(AggiornamentoCliente)
                .where(AggiornamentoCliente.stato == "bozza")
            ),
            "budget_ai": stato_budget(db),
            "oggi": oggi,
        },
    )


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    """Stato dell'applicazione e del database. Unica route pubblica insieme al login."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("Health check: database non raggiungibile")
        return JSONResponse({"stato": "errore", "database": "non raggiungibile"}, status_code=503)
    return JSONResponse({"stato": "ok", "database": "ok"})
