"""Statistiche dello studio: pratiche, esiti, importi, responsabili, modelli, segnalatori,
prefattibilità. Solo letture aggregate dal database, nessun dato esce dal server."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.auth.dipendenze import utente_corrente
from app.db import get_db
from app.models import Azienda, ModelloPratica, Pratica, Prefattibilita, Segnalatore, Utente
from app.pratiche.prefattibilita import ETICHETTE_ESITO
from app.pratiche.servizio import ETICHETTE_STATO_PRATICA

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]

STATI_POSITIVI = ("ammessa", "rendicontazione", "chiusa")


def _d(v: Any) -> Decimal:
    return Decimal(v or 0)


def calcola(db: Session, anno: int | None) -> dict[str, Any]:
    filtro = [extract("year", Pratica.creato_il) == anno] if anno else []
    per_stato = dict(
        db.execute(select(Pratica.stato, func.count()).where(*filtro).group_by(Pratica.stato)).all()
    )
    totale = sum(per_stato.values())
    richiesto, concesso = db.execute(
        select(
            func.coalesce(func.sum(Pratica.importo_richiesto), 0),
            func.coalesce(func.sum(Pratica.importo_concesso), 0),
        ).where(*filtro)
    ).one()
    positive = sum(per_stato.get(s, 0) for s in STATI_POSITIVI)
    respinte = per_stato.get("respinta", 0)
    concluse = positive + respinte
    # Con "chiusa" si conta anche una pratica chiusa senza esito: il tasso usa l'importo
    # concesso per distinguere le chiuse con contributo.
    tasso = round(100 * positive / concluse) if concluse else None

    def gruppo(colonna: Any, etichetta: Any, join: Any = None) -> list[dict[str, Any]]:
        q = select(
            etichetta,
            func.count(Pratica.id),
            func.coalesce(func.sum(Pratica.importo_richiesto), 0),
            func.coalesce(func.sum(Pratica.importo_concesso), 0),
        ).select_from(Pratica)
        if join is not None:
            q = q.outerjoin(*join)
        q = q.where(*filtro).group_by(etichetta).order_by(func.count(Pratica.id).desc())
        return [
            {"nome": n or "—", "pratiche": c, "richiesto": _d(r), "concesso": _d(k)}
            for n, c, r, k in db.execute(q).all()
        ]

    per_responsabile = gruppo(
        Pratica.responsabile_id, Utente.nome, (Utente, Pratica.responsabile_id == Utente.id)
    )
    per_modello = gruppo(
        Pratica.modello_id,
        ModelloPratica.nome,
        (ModelloPratica, Pratica.modello_id == ModelloPratica.id),
    )
    q_segn = (
        select(
            Segnalatore.nome,
            func.count(Pratica.id),
            func.coalesce(func.sum(Pratica.importo_richiesto), 0),
            func.coalesce(func.sum(Pratica.importo_concesso), 0),
        )
        .select_from(Pratica)
        .join(Azienda, Pratica.azienda_id == Azienda.id)
        .join(Segnalatore, Azienda.segnalatore_id == Segnalatore.id)
        .where(*filtro)
        .group_by(Segnalatore.nome)
        .order_by(func.count(Pratica.id).desc())
    )
    per_segnalatore = [
        {"nome": n, "pratiche": c, "richiesto": _d(r), "concesso": _d(k)}
        for n, c, r, k in db.execute(q_segn).all()
    ]
    mese = func.date_trunc("month", Pratica.creato_il)
    per_mese = [
        {"mese": m.date(), "pratiche": c}
        for m, c in db.execute(
            select(mese, func.count()).where(*filtro).group_by(mese).order_by(mese)
        ).all()
    ][-12:]
    filtro_pf = [extract("year", Prefattibilita.creato_il) == anno] if anno else []
    prefattibilita = dict(
        db.execute(
            select(Prefattibilita.esito, func.count())
            .where(*filtro_pf)
            .group_by(Prefattibilita.esito)
        ).all()
    )
    pf_convertite = db.scalar(
        select(func.count())
        .select_from(Prefattibilita)
        .where(Prefattibilita.pratica_id.is_not(None), *filtro_pf)
    )
    massimo_mese = max((m["pratiche"] for m in per_mese), default=0)
    return {
        "totale": totale,
        "per_stato": per_stato,
        "richiesto": _d(richiesto),
        "concesso": _d(concesso),
        "tasso": tasso,
        "concluse": concluse,
        "per_responsabile": per_responsabile,
        "per_modello": per_modello,
        "per_segnalatore": per_segnalatore,
        "per_mese": per_mese,
        "massimo_mese": massimo_mese,
        "prefattibilita": prefattibilita,
        "pf_convertite": pf_convertite,
        "pf_totale": sum(prefattibilita.values()),
    }


@router.get("/statistiche")
def statistiche(request: Request, db: Db, _: Autenticato) -> Response:
    valore = request.query_params.get("anno") or ""
    anno = int(valore) if valore.isdigit() else None
    anni = [
        int(a)
        for (a,) in db.execute(
            select(extract("year", Pratica.creato_il).label("a")).distinct().order_by("a")
        ).all()
    ]
    if date.today().year not in anni:
        anni.append(date.today().year)
    from app.opportunita.valutazione import precisione
    from app.web import render

    return render(
        request,
        "statistiche.html",
        {
            "s": calcola(db, anno),
            "anno": anno,
            "anni": sorted(anni, reverse=True),
            "etichette_stato": ETICHETTE_STATO_PRATICA,
            "etichette_pf": ETICHETTE_ESITO,
            "precisione": precisione(db),
        },
    )
