"""Job notturni via HTTP, per le piattaforme senza un worker sempre acceso (Vercel Cron).

Vercel chiama GET /cron/<job> agli orari di vercel.json con l'intestazione
`Authorization: Bearer <CRON_SECRET>`. Senza CRON_SECRET le rotte non esistono (404),
quindi su un server con il worker (docker-compose) restano spente.
Ogni esecuzione passa da esegui_job: stesso advisory lock e stessa riga in job_run del worker.
"""

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.jobs import attivita
from app.jobs.esecuzione import esegui_job

JOB: dict[str, tuple[str, Callable[[], str]]] = {
    "raccolta-bandi": ("raccolta_bandi", attivita.raccolta_bandi),
    "raccolta-appalti": ("raccolta_appalti", attivita.raccolta_appalti),
    "documenti-bandi": ("documenti_bandi", attivita.documenti_bandi),
    "estrazione-bandi": ("estrazione_bandi", attivita.estrazione_bandi),
    "opportunita": ("opportunita", attivita.opportunita),
    "promemoria": ("promemoria", attivita.promemoria_scadenze),
    "pulizia": ("pulizia", attivita.pulizia),
    "riferimenti-istat": ("riferimenti_istat", attivita.riferimenti_istat),
    "backup": ("backup_esterno", attivita.backup_esterno),
}

router = APIRouter(prefix="/cron")


@router.get("/{nome}")
def esegui(nome: str, authorization: Annotated[str | None, Header()] = None) -> JSONResponse:
    segreto = get_settings().cron_secret
    if segreto is None or nome not in JOB:
        raise HTTPException(status_code=404)
    atteso = f"Bearer {segreto.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization.encode(), atteso.encode()):
        raise HTTPException(status_code=401)
    job, funzione = JOB[nome]
    esito = esegui_job(job, funzione)
    return JSONResponse({"job": job, "esito": esito}, headers={"Cache-Control": "no-store"})
