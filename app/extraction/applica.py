"""Scrittura dei requisiti risolti in bando_requisiti (usata dalla raccolta, dall'estrazione e
dalle correzioni manuali)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.extraction.risoluzione import COLONNE, risolvi
from app.models import Bando, BandoRequisiti
from app.models.ai import BandoOverride, EstrazioneBando


def _json(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def valori_da_fonte(rf: Any) -> dict[str, Any]:
    """RequisitiFonte → dizionario serializzabile da salvare in valori_fonte."""
    valori = {c: _json(getattr(rf, c, None)) for c in COLONNE if hasattr(rf, c)}
    valori["_da_verificare"] = list(rf.da_verificare or [])
    return valori


def valori_dai_requisiti(req: BandoRequisiti) -> dict[str, Any]:
    """Per i bandi importati prima della fase 3 (senza valori_fonte): i valori attuali con
    provenienza "fonte" sono i dati della fonte."""
    prov = req.provenienza or {}
    valori = {
        c: _json(getattr(req, c))
        for c in COLONNE
        if prov.get(c) == "fonte" or (c == "territorio" and req.territorio != "sconosciuto")
    }
    valori["_da_verificare"] = list(req.da_verificare or [])
    return valori


def carica_ai_e_manuali(
    db: Session, bando_ids: list[int] | None = None
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Ultima estrazione riuscita e correzioni manuali, per bando."""
    q = (
        select(EstrazioneBando.bando_id, EstrazioneBando.output)
        .where(EstrazioneBando.output.is_not(None))
        .order_by(EstrazioneBando.bando_id, EstrazioneBando.id.desc())
        .ext(distinct_on(EstrazioneBando.bando_id))
    )
    qo = select(BandoOverride)
    if bando_ids is not None:
        q = q.where(EstrazioneBando.bando_id.in_(bando_ids))
        qo = qo.where(BandoOverride.bando_id.in_(bando_ids))
    ai = {bid: out for bid, out in db.execute(q).all()}
    manuali: dict[int, dict[str, Any]] = {}
    for o in db.scalars(qo):
        manuali.setdefault(o.bando_id, {})[o.campo] = o.valore
    return ai, manuali


def scrivi(
    bando: Bando,
    valori_fonte: dict[str, Any] | None,
    ai: dict[str, Any] | None,
    manuali: dict[str, Any] | None,
) -> None:
    """Risolve e scrive i requisiti; con dati AI applica anche le date mancanti del bando."""
    req = bando.requisiti or BandoRequisiti()
    fonte = dict(valori_fonte or {})
    incerti = list(fonte.pop("_da_verificare", []) or [])
    valori, provenienza, da_verificare = risolvi(fonte, incerti, ai, manuali)
    for col, v in valori.items():
        setattr(req, col, v)
    # Date: solo se la fonte non le dà (es. portali regionali con lo stato dichiarato)
    manuali = manuali or {}
    ai = ai or {}
    for campo in ("data_apertura", "data_chiusura"):
        nuovo = None
        if campo in manuali and manuali[campo]:
            nuovo, origine = manuali[campo], "manuale"
        elif getattr(bando, campo) is None or provenienza.get(campo) == "llm":
            v = ai.get(campo) or {}
            if v.get("valore") and v.get("confidenza") in ("alta", "media"):
                nuovo, origine = v["valore"], "llm"
        if nuovo:
            setattr(bando, campo, date.fromisoformat(str(nuovo)[:10]))
            provenienza[campo] = origine
    if provenienza.get("data_apertura") or provenienza.get("data_chiusura"):
        da_verificare = [d for d in da_verificare if d != "date"]
    req.valori_fonte = valori_fonte
    req.provenienza = provenienza
    req.da_verificare = da_verificare or None
    req.risolto_il = datetime.now(UTC)
    bando.requisiti = req


def ricalcola(db: Session, bando: Bando, oggi: date | None = None) -> None:
    """Dopo un'estrazione o una correzione: requisiti e stato aggiornati."""
    from app.bandi.stato import calcola_stato

    ai, manuali = carica_ai_e_manuali(db, [bando.id])
    valori_fonte = bando.requisiti.valori_fonte if bando.requisiti else None
    if valori_fonte is None and bando.requisiti is not None:
        valori_fonte = valori_dai_requisiti(bando.requisiti)
    scrivi(bando, valori_fonte, ai.get(bando.id), manuali.get(bando.id))
    bando.stato = calcola_stato(
        bando.data_apertura,
        bando.data_chiusura,
        oggi or date.today(),
        scomparso=bando.scomparso_dal_feed_il is not None,
        stato_fonte=bando.stato_fonte,
    )
