"""Export completo dei dati del cliente (brief: «i dati restano del cliente»): un unico zip
con un CSV per ogni tabella del CRM e delle pratiche, più tutti gli allegati.

Solo il titolare; ogni export finisce nell'audit. Non contiene segreti (password, chiavi API
cifrate, sessioni) né i dati pubblici dei bandi, che si possono riscaricare.
"""

import csv
import io
import os
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app import audit
from app import models as m
from app.auth.dipendenze import utente_corrente
from app.config import get_settings
from app.db import get_db
from app.web import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
# Chi può esportare lo decide il titolare (sezione «esportazione», app/auth/permessi.py)
Titolare = Annotated[m.Utente, Depends(utente_corrente)]

# Tabelle esportate: nome del file → modello
TABELLE: dict[str, Any] = {
    "aziende": m.Azienda,
    "sedi": m.Sede,
    "referenti": m.Referente,
    "ateco_aziende": m.AziendaAteco,
    "tag": m.Tag,
    "segnalatori": m.Segnalatore,
    "progetti": m.Progetto,
    "attivita": m.Attivita,
    "scadenze": m.Scadenza,
    "aiuti_de_minimis": m.AiutoDeMinimis,
    "pratiche": m.Pratica,
    "pratiche_fasi": m.PraticaFase,
    "pratiche_operazioni": m.PraticaOperazione,
    "pratiche_documenti": m.PraticaDocumento,
    "pratiche_dati": m.PraticaDato,
    "pratiche_eventi": m.PraticaEvento,
    "prefattibilita": m.Prefattibilita,
    "allegati": m.Allegato,
    "opportunita": m.Opportunita,
    "aggiornamenti_clienti": m.AggiornamentoCliente,
    "report_bandi": m.ReportBando,
    "note": m.Commento,
    "bandi_seguiti": m.Seguito,
    "modelli_pratica": m.ModelloPratica,
    "modelli_fasi": m.ModelloFase,
    "modelli_operazioni": m.ModelloOperazione,
}
# Colonne da non esportare mai
ESCLUSE = {"password_hash", "totp_secret_cifrato", "segreti", "token_hash"}


def _cella(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v:.2f}".replace(".", ",")
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, list | dict):
        import json

        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def csv_tabella(db: Session, modello: Any) -> str:
    colonne = [c.key for c in modello.__table__.columns if c.key not in ESCLUSE]
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";")
    w.writerow(colonne)
    for riga in db.scalars(
        select(modello).order_by(modello.__table__.primary_key.columns.values()[0])
    ):
        w.writerow([_cella(getattr(riga, c)) for c in colonne])
    return "﻿" + buffer.getvalue()


def crea_zip(db: Session, destinazione: Path) -> dict[str, int]:
    conteggi: dict[str, int] = {}
    cartella = Path(get_settings().cartella_allegati).resolve()
    with zipfile.ZipFile(destinazione, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, modello in TABELLE.items():
            testo = csv_tabella(db, modello)
            z.writestr(f"dati/{nome}.csv", testo.encode("utf-8"))
            conteggi[nome] = testo.count("\n") - 1
        allegati = 0
        for a in db.scalars(select(m.Allegato)):
            nome_zip = f"allegati/{a.id}_{Path(a.nome_originale).name}"
            if a.percorso.startswith("db/"):  # ARCHIVIO_ALLEGATI=database
                if a.contenuto is not None:
                    z.writestr(nome_zip, a.contenuto.dati)
                    allegati += 1
                db.expire(a, ["contenuto"])  # non tenere in memoria tutti i file
                continue
            percorso = (cartella / a.percorso).resolve()
            if percorso.is_file() and cartella in percorso.parents:
                z.write(percorso, nome_zip)
                allegati += 1
        conteggi["file_allegati"] = allegati
        z.writestr(
            "LEGGIMI.txt",
            "Export completo di Europrojecta Studio del "
            f"{datetime.now():%d/%m/%Y %H:%M}.\n\n"
            "dati/: un file CSV per tabella (separatore ';', codifica UTF-8, importi con la "
            "virgola, date in formato AAAA-MM-GG). Le colonne *_id collegano le tabelle.\n"
            "allegati/: i file caricati nelle pratiche, con l'id dell'allegato davanti al nome.\n",
        )
    return conteggi


@router.get("/export")
def pagina(request: Request, _: Titolare) -> Response:
    return render(request, "export.html", {"tabelle": list(TABELLE)})


@router.get("/export/completo.zip")
def scarica(db: Db, utente: Titolare) -> Response:
    descrittore, nome = tempfile.mkstemp(prefix="export-", suffix=".zip")
    os.close(descrittore)
    percorso = Path(nome)
    conteggi = crea_zip(db, percorso)
    audit.registra(db, utente, "export", None, "esporta", conteggi)
    db.commit()
    return FileResponse(
        percorso,
        media_type="application/zip",
        filename=f"europrojecta-export-{date.today():%Y%m%d}.zip",
        background=BackgroundTask(percorso.unlink, missing_ok=True),
    )
