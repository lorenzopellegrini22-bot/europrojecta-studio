"""Applicazione FastAPI: montaggio dei router e /health.

Gli endpoint sono sincroni (def, non async def): con un solo utente l'async non serve.
"""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import NOME_APP
from app.db import get_db

log = logging.getLogger(__name__)

app = FastAPI(title=NOME_APP, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    """Stato dell'applicazione e del database. Unica route pubblica insieme al login."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("Health check: database non raggiungibile")
        return JSONResponse({"stato": "errore", "database": "non raggiungibile"}, status_code=503)
    return JSONResponse({"stato": "ok", "database": "ok"})
