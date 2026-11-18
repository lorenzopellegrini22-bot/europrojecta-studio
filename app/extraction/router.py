"""Impostazioni → Intelligenza artificiale, e revisione dei campi estratti dai bandi."""

import contextlib
import html
import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.extraction import servizio
from app.extraction.applica import ricalcola
from app.extraction.schema import CAMPI, PER_NOME
from app.models import Bando, BandoRequisiti, Utente
from app.models.ai import BandoOverride, EstrazioneBando, UsoLLM
from app.utils.numeri import importo_italiano
from app.utils.ricerca import normalizza
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]


# --- Impostazioni ------------------------------------------------------------------------


@router.get("/impostazioni/ai")
def pagina_impostazioni(request: Request, db: Db, _: Titolare) -> Response:
    imp = servizio.impostazioni(db)
    conf, motivo = servizio.configurazione(db)
    oggi = date.today()
    inizio = date(oggi.year, oggi.month, 1)
    per_operazione = db.execute(
        select(
            UsoLLM.operazione,
            func.count(),
            func.coalesce(func.sum(UsoLLM.costo_stimato), 0),
        )
        .where(func.date(UsoLLM.creato_il) >= inizio)
        .group_by(UsoLLM.operazione)
    ).all()
    return render(
        request,
        "impostazioni/ai.html",
        {
            "imp": imp,
            "pronta": conf is not None,
            "motivo": motivo,
            "budget": servizio.stato_budget(db),
            "per_operazione": per_operazione,
            "in_coda": len(servizio.da_estrarre(db, conf.modello, 10_000)) if conf else None,
            "salvato": request.query_params.get("salvato"),
            "messaggio": request.query_params.get("msg"),
        },
    )


@router.post("/impostazioni/ai", dependencies=CSRF)
def salva_impostazioni(db: Db, utente: Titolare, form: Form) -> Response:
    imp = servizio.impostazioni(db)
    for campo in ("modello_estrazione", "modello_scoring", "modello_embeddings"):
        setattr(imp, campo, (testo(form, campo) or "")[:100] or None)
    budget = importo_italiano(testo(form, "budget_mensile_eur"))
    imp.budget_mensile_eur = budget if budget is not None and budget >= 0 else None
    with contextlib.suppress(ValueError):
        imp.estrazioni_per_notte = max(0, min(int(testo(form, "estrazioni_per_notte") or 50), 2000))
    prezzi: dict[str, Any] = {}
    for i in range(6):
        nome = testo(form, f"prezzo_modello_{i}")
        p_in = importo_italiano(testo(form, f"prezzo_ingresso_{i}"))
        p_out = importo_italiano(testo(form, f"prezzo_uscita_{i}"))
        if nome and p_in is not None and p_out is not None:
            prezzi[nome[:100]] = {"ingresso": str(p_in), "uscita": str(p_out)}
    imp.prezzi = prezzi
    if form.get("riattiva") == "1":
        imp.sospesa_fino_al = None
        imp.motivo_sospensione = None
    audit.registra(db, utente, "impostazioni_ai", 1, "modifica", {"modelli_e_budget": True})
    db.commit()
    return RedirectResponse("/impostazioni/ai?salvato=1", status_code=303)


@router.post("/impostazioni/ai/esegui", dependencies=CSRF)
def esegui_ora(background: BackgroundTasks, _: Titolare) -> Response:
    from app.jobs import attivita
    from app.jobs.esecuzione import esegui_job

    background.add_task(esegui_job, "estrazione_bandi", attivita.estrazione_bandi)
    return RedirectResponse(
        "/impostazioni/ai?msg="
        + quote("Estrazione avviata: i risultati arrivano in pochi minuti."),
        status_code=303,
    )


# --- Revisione ---------------------------------------------------------------------------


@router.get("/revisione")
def lista_revisione(request: Request, db: Db, _: Autenticato) -> Response:
    """Bandi aperti o in arrivo con campi estratti dall'AI ancora da verificare."""
    righe = db.execute(
        select(Bando, BandoRequisiti.da_verificare)
        .join(BandoRequisiti)
        .where(
            Bando.stato.in_(("aperto", "in_arrivo")),
            BandoRequisiti.da_verificare.is_not(None),
            Bando.id.in_(
                select(EstrazioneBando.bando_id).where(EstrazioneBando.output.is_not(None))
            ),
        )
        .order_by(Bando.data_chiusura.asc().nulls_last())
        .limit(200)
    ).all()
    return render(request, "revisione/lista.html", {"righe": righe})


def _evidenzia(testo_bando: str, citazioni: list[str]) -> Markup:
    """Il testo del bando con le citazioni evidenziate (confronto senza accenti/maiuscole)."""
    sicuro = html.escape(testo_bando)
    for c in sorted({c for c in citazioni if c and len(c) > 8}, key=len, reverse=True):
        parole = [re.escape(html.escape(p)) for p in c.split()[:40]]
        if not parole:
            continue
        modello = re.compile(r"\W+".join(parole), re.IGNORECASE)
        sicuro = modello.sub(lambda m: f"<mark>{m.group(0)}</mark>", sicuro, count=1)
    return Markup(sicuro.replace("\n", "<br>"))


@router.get("/revisione/{bando_id}")
def scheda_revisione(request: Request, bando_id: int, db: Db, _: Autenticato) -> Response:
    b = db.get(Bando, bando_id, options=[selectinload(Bando.requisiti)])
    if b is None:
        raise HTTPException(404)
    estrazione = db.scalar(
        select(EstrazioneBando)
        .where(EstrazioneBando.bando_id == b.id, EstrazioneBando.output.is_not(None))
        .order_by(EstrazioneBando.id.desc())
        .limit(1)
    )
    manuali = {
        o.campo: o for o in db.scalars(select(BandoOverride).where(BandoOverride.bando_id == b.id))
    }
    req = b.requisiti
    output = estrazione.output if estrazione else {}
    campi = []
    for c in CAMPI:
        if c.nome in ("ente", "altri_requisiti"):
            continue
        attuale = (
            getattr(b, c.nome)
            if c.nome in ("data_apertura", "data_chiusura")
            else (getattr(req, c.colonna) if req and c.colonna else None)
        )
        campi.append(
            {
                "campo": c,
                "ai": output.get(c.nome) or {},
                "attuale": attuale,
                "provenienza": (req.provenienza or {}).get(c.colonna or c.nome) if req else None,
                "da_verificare": bool(req and (c.colonna or c.nome) in (req.da_verificare or [])),
                "manuale": manuali.get(c.colonna or c.nome),
            }
        )
    testo_bando = servizio.testo_bando(b, db)
    return render(
        request,
        "revisione/scheda.html",
        {
            "b": b,
            "estrazione": estrazione,
            "campi": campi,
            "altri": (output.get("altri_requisiti") or {}).get("valore"),
            "testo": _evidenzia(testo_bando, [(v or {}).get("citazione") for v in output.values()]),
        },
    )


def _valore_da_form(nome: str, grezzo: str | None) -> Any:
    c = PER_NOME[nome]
    if grezzo is None or grezzo.strip() == "":
        return None
    if c.tipo == "lista":
        voci = [v.strip() for v in re.split(r"[\n;]+", grezzo) if v.strip()]
        if c.valori:
            ammessi = {normalizza(v): v for v in c.valori}
            voci = [ammessi[normalizza(v)] for v in voci if normalizza(v) in ammessi]
        return voci or None
    if c.tipo == "scelta":
        return grezzo if grezzo in c.valori else None
    if c.tipo in ("numero", "intero"):
        d = importo_italiano(grezzo)
        if d is None:
            return None
        return int(d) if c.tipo == "intero" else str(d)
    if c.tipo == "data":
        try:
            return date.fromisoformat(grezzo).isoformat()
        except ValueError:
            return None
    return grezzo.strip()[:2000]


@router.post("/revisione/{bando_id}", dependencies=CSRF)
def salva_revisione(bando_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    """Per ogni campo: «conferma» fissa il valore attuale, «correggi» salva il nuovo valore,
    «annulla» toglie la correzione manuale. Le correzioni prevalgono su fonte e AI."""
    b = db.get(Bando, bando_id, options=[selectinload(Bando.requisiti)])
    if b is None:
        raise HTTPException(404)
    nome = testo(form, "campo") or ""
    if nome not in PER_NOME:
        raise HTTPException(400)
    c = PER_NOME[nome]
    chiave = c.colonna or nome
    azione = testo(form, "azione")
    esistente = db.scalar(
        select(BandoOverride).where(BandoOverride.bando_id == b.id, BandoOverride.campo == chiave)
    )
    if azione == "annulla":
        if esistente:
            db.delete(esistente)
    else:
        if azione == "conferma":
            if c.nome in ("data_apertura", "data_chiusura"):
                v = getattr(b, c.nome)
                valore: Any = v.isoformat() if v else None
            else:
                v = getattr(b.requisiti, c.colonna) if b.requisiti else None
                valore = str(v) if isinstance(v, Decimal) else v
        else:
            valore = _valore_da_form(nome, testo(form, "valore"))
        if esistente:
            esistente.valore = valore
            esistente.utente_id = utente.id
            esistente.nota = testo(form, "nota")
        else:
            db.add(
                BandoOverride(
                    bando_id=b.id,
                    campo=chiave,
                    valore=valore,
                    nota=testo(form, "nota"),
                    utente_id=utente.id,
                )
            )
    db.flush()
    ricalcola(db, b)
    estrazione = db.scalar(
        select(EstrazioneBando)
        .where(EstrazioneBando.bando_id == b.id, EstrazioneBando.output.is_not(None))
        .order_by(EstrazioneBando.id.desc())
        .limit(1)
    )
    if estrazione and not (b.requisiti and b.requisiti.da_verificare):
        estrazione.stato_revisione = "revisionato"
    audit.registra(db, utente, "bando", b.id, "modifica", {"revisione": chiave, "azione": azione})
    db.commit()
    return RedirectResponse(f"/revisione/{b.id}#campo-{nome}", status_code=303)
