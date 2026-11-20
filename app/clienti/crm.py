"""Scheda cliente, parti del CRM completo: progetti d'investimento, attività, scadenze,
aiuti de minimis ricevuti, consensi dei referenti agli aggiornamenti."""

from datetime import UTC, date, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.db import get_db
from app.models import Azienda, Utente
from app.models.crm_esteso import (
    CATEGORIE_SPESA,
    REGOLAMENTI_DE_MINIMIS,
    STATI_PROGETTO,
    TIPI_ATTIVITA,
    AggiornamentoCliente,
    AiutoDeMinimis,
    Attivita,
    Progetto,
    Scadenza,
)
from app.utils.numeri import importo_italiano
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

ETICHETTE_PROGETTO = {
    "idea": "Idea",
    "in_valutazione": "In valutazione",
    "in_corso": "In corso",
    "concluso": "Concluso",
    "abbandonato": "Abbandonato",
}
ETICHETTE_ATTIVITA = {
    "nota": "Nota",
    "chiamata": "Chiamata",
    "email": "Email",
    "incontro": "Incontro",
}
ETICHETTE_REGOLAMENTO = {
    "generale": "Generale (Reg. UE 2023/2831)",
    "sieg": "SIEG (Reg. UE 2023/2832)",
    "agricolo": "Agricolo",
    "pesca": "Pesca e acquacoltura",
}


def _azienda(db: Session, azienda_id: int) -> Azienda:
    a = db.get(Azienda, azienda_id)
    if a is None:
        raise HTTPException(404)
    return a


def _data(valore: str | None) -> date | None:
    try:
        return date.fromisoformat(valore) if valore else None
    except ValueError:
        return None


def _lista(form: dict[str, Any], nome: str) -> list[str]:
    v = form.get(nome) or []
    return [v] if isinstance(v, str) else list(v)


def _torna(
    azienda_id: int, ancora: str, msg: str | None = None, cestino: int | None = None
) -> Response:
    parametri = [f"msg={quote(msg)}"] if msg else []
    if cestino is not None:  # «spostato nel cestino · Annulla» (base.html)
        parametri.append(f"cestino={cestino}")
    extra = "?" + "&".join(parametri) if parametri else ""
    return RedirectResponse(f"/clienti/{azienda_id}{extra}#{ancora}", status_code=303)


def _nel_cestino(
    db: Session, utente: Utente, oggetto: Any, tipo: str, descrizione: str, ancora: str
) -> int:
    from app.cestino import servizio as cestino

    link = f"/clienti/{oggetto.azienda_id}#{ancora}"
    return cestino.sposta(db, utente, oggetto, tipo, descrizione, link).id


# --- Progetti d'investimento ----------------------------------------------------------


def _valori_progetto(form: dict[str, Any]) -> dict[str, Any] | None:
    titolo = testo(form, "titolo")
    if not titolo:
        return None
    stato = testo(form, "stato") or "idea"
    categorie = [c for c in _lista(form, "categorie") if c in CATEGORIE_SPESA]
    importo = importo_italiano(testo(form, "importo_stimato"))
    return {
        "titolo": titolo[:300],
        "descrizione": testo(form, "descrizione"),
        "importo_stimato": importo if importo is not None and importo >= 0 else None,
        "inizio_previsto": _data(testo(form, "inizio_previsto")),
        "fine_prevista": _data(testo(form, "fine_prevista")),
        "stato": stato if stato in STATI_PROGETTO else "idea",
        "categorie": categorie or None,
    }


@router.post("/clienti/{azienda_id}/progetti", dependencies=CSRF)
def nuovo_progetto(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    valori = _valori_progetto(form)
    if valori is None:
        return _torna(a.id, "progetti", "Il titolo del progetto è obbligatorio.")
    p = Progetto(azienda_id=a.id, **valori)
    db.add(p)
    db.flush()
    audit.registra(db, utente, "progetto", p.id, "crea", {"titolo": p.titolo})
    db.commit()
    return _torna(a.id, "progetti")


@router.get("/clienti/{azienda_id}/progetti/{progetto_id}")
def modifica_progetto(
    request: Request, azienda_id: int, progetto_id: int, db: Db, _: Autenticato
) -> Response:
    p = db.get(Progetto, progetto_id)
    if p is None or p.azienda_id != azienda_id:
        raise HTTPException(404)
    return render(
        request,
        "clienti/progetto.html",
        {
            "p": p,
            "azienda": p.azienda,
            "categorie": CATEGORIE_SPESA,
            "stati": ETICHETTE_PROGETTO,
        },
    )


@router.post("/clienti/{azienda_id}/progetti/{progetto_id}", dependencies=CSRF)
def salva_progetto(
    azienda_id: int, progetto_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    p = db.get(Progetto, progetto_id)
    if p is None or p.azienda_id != azienda_id:
        raise HTTPException(404)
    if form.get("elimina") == "1":
        audit.registra(db, utente, "progetto", p.id, "elimina", {"titolo": p.titolo})
        eid = _nel_cestino(db, utente, p, "progetto", p.titolo, "progetti")
        db.delete(p)
        db.commit()
        return _torna(azienda_id, "progetti", cestino=eid)
    valori = _valori_progetto(form)
    if valori is None:
        return RedirectResponse(
            f"/clienti/{azienda_id}/progetti/{p.id}?msg={quote('Il titolo è obbligatorio.')}",
            status_code=303,
        )
    differenze = {k: str(v) for k, v in valori.items() if getattr(p, k) != v}
    for k, v in valori.items():
        setattr(p, k, v)
    if differenze:
        audit.registra(db, utente, "progetto", p.id, "modifica", differenze)
    db.commit()
    return _torna(azienda_id, "progetti")


# --- Attività -----------------------------------------------------------------------------


@router.post("/clienti/{azienda_id}/attivita", dependencies=CSRF)
def nuova_attivita(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    tipo = testo(form, "tipo") or "nota"
    contenuto = testo(form, "testo")
    if tipo not in TIPI_ATTIVITA or not contenuto:
        return _torna(a.id, "attivita", "Scrivi il testo dell'attività.")
    db.add(
        Attivita(
            azienda_id=a.id,
            tipo=tipo,
            data=_data(testo(form, "data")) or date.today(),
            testo=contenuto,
            utente_id=utente.id,
            progetto_id=int(form["progetto_id"]) if testo(form, "progetto_id") else None,
        )
    )
    db.commit()
    return _torna(a.id, "attivita")


@router.post("/clienti/{azienda_id}/attivita/{attivita_id}/elimina", dependencies=CSRF)
def elimina_attivita(azienda_id: int, attivita_id: int, db: Db, utente: Autenticato) -> Response:
    x = db.get(Attivita, attivita_id)
    if x is None or x.azienda_id != azienda_id:
        raise HTTPException(404)
    audit.registra(db, utente, "attivita", x.id, "elimina", {"testo": x.testo[:200]})
    eid = _nel_cestino(db, utente, x, "attività", x.testo[:80], "attivita")
    db.delete(x)
    db.commit()
    return _torna(azienda_id, "attivita", cestino=eid)


# --- Scadenze (promemoria) -----------------------------------------------------------------


@router.post("/clienti/{azienda_id}/scadenze", dependencies=CSRF)
def nuova_scadenza(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    titolo = testo(form, "titolo")
    quando = _data(testo(form, "data"))
    if not titolo or quando is None:
        return _torna(a.id, "scadenze", "Indica titolo e data della scadenza.")
    db.add(Scadenza(azienda_id=a.id, titolo=titolo[:300], data=quando))
    db.commit()
    return _torna(a.id, "scadenze")


@router.post("/clienti/{azienda_id}/scadenze/{scadenza_id}", dependencies=CSRF)
def aggiorna_scadenza(
    azienda_id: int, scadenza_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    s = db.get(Scadenza, scadenza_id)
    if s is None or s.azienda_id != azienda_id:
        raise HTTPException(404)
    azione = testo(form, "azione")
    eid = None
    if azione == "completa":
        s.completata, s.completata_il = True, datetime.now(UTC)
    elif azione == "riapri":
        s.completata, s.completata_il = False, None
    elif azione == "elimina":
        audit.registra(db, utente, "scadenza", s.id, "elimina", {"titolo": s.titolo})
        eid = _nel_cestino(db, utente, s, "promemoria", s.titolo, "scadenze")
        db.delete(s)
    db.commit()
    torna = testo(form, "torna")
    if torna and torna.startswith("/"):
        if eid is not None:
            from app.cestino.servizio import con_avviso_id

            torna = con_avviso_id(torna, eid)
        return RedirectResponse(torna, status_code=303)
    return _torna(azienda_id, "scadenze", cestino=eid)


# --- De minimis ----------------------------------------------------------------------------


@router.post("/clienti/{azienda_id}/de-minimis", dependencies=CSRF)
def nuovo_aiuto(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    quando = _data(testo(form, "data_concessione"))
    importo = importo_italiano(testo(form, "importo"))
    regolamento = testo(form, "regolamento") or "generale"
    if (
        quando is None
        or importo is None
        or importo <= 0
        or regolamento not in REGOLAMENTI_DE_MINIMIS
    ):
        return _torna(a.id, "de-minimis", "Indica data di concessione e importo dell'aiuto.")
    aiuto = AiutoDeMinimis(
        azienda_id=a.id,
        data_concessione=quando,
        importo=importo,
        regolamento=regolamento,
        ente=testo(form, "ente"),
        descrizione=testo(form, "descrizione"),
    )
    db.add(aiuto)
    db.flush()
    audit.registra(db, utente, "aiuto_de_minimis", aiuto.id, "crea", {"importo": str(importo)})
    db.commit()
    return _torna(a.id, "de-minimis")


@router.post("/clienti/{azienda_id}/de-minimis/{aiuto_id}/elimina", dependencies=CSRF)
def elimina_aiuto(azienda_id: int, aiuto_id: int, db: Db, utente: Autenticato) -> Response:
    x = db.get(AiutoDeMinimis, aiuto_id)
    if x is None or x.azienda_id != azienda_id:
        raise HTTPException(404)
    audit.registra(db, utente, "aiuto_de_minimis", x.id, "elimina", {"importo": str(x.importo)})
    descrizione = f"{x.ente or 'Aiuto'} del {x.data_concessione:%d/%m/%Y}"
    eid = _nel_cestino(db, utente, x, "aiuto de minimis", descrizione, "de-minimis")
    db.delete(x)
    db.commit()
    return _torna(azienda_id, "de-minimis", cestino=eid)


# --- Consensi dei referenti ---------------------------------------------------------------


@router.post("/clienti/{azienda_id}/referenti/{referente_id}/consensi", dependencies=CSRF)
def consensi(
    azienda_id: int, referente_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    a = db.get(Azienda, azienda_id, options=[selectinload(Azienda.referenti)])
    r = next((x for x in (a.referenti if a else []) if x.id == referente_id), None)
    if r is None:
        raise HTTPException(404)
    email, whatsapp = form.get("consenso_email") == "1", form.get("consenso_whatsapp") == "1"
    canale = testo(form, "canale_preferito")
    if (email, whatsapp) != (r.consenso_email, r.consenso_whatsapp):
        r.consenso_il = datetime.now(UTC) if (email or whatsapp) else None
        if email or whatsapp:
            r.disiscritto_il = None
    r.consenso_email, r.consenso_whatsapp = email, whatsapp
    r.canale_preferito = canale if canale in ("email", "whatsapp") else None
    audit.registra(
        db,
        utente,
        "referente",
        r.id,
        "modifica",
        {"consenso_email": email, "consenso_whatsapp": whatsapp, "canale": r.canale_preferito},
    )
    db.commit()
    return _torna(azienda_id, "referenti")


def dati_scheda(db: Session, a: Azienda) -> dict[str, Any]:
    """Dati del CRM completo per la scheda cliente."""
    from app.utils.impresa import dimensione_effettiva, plafond_residuo

    aiuti = list(
        db.scalars(
            select(AiutoDeMinimis)
            .where(AiutoDeMinimis.azienda_id == a.id)
            .order_by(AiutoDeMinimis.data_concessione.desc())
        )
    )
    dimensione, provenienza = dimensione_effettiva(a)
    return {
        "progetti": list(
            db.scalars(
                select(Progetto).where(Progetto.azienda_id == a.id).order_by(Progetto.creato_il)
            )
        ),
        "attivita": list(
            db.scalars(
                select(Attivita)
                .where(Attivita.azienda_id == a.id)
                .order_by(Attivita.data.desc(), Attivita.id.desc())
                .limit(50)
            )
        ),
        "scadenze": list(
            db.scalars(
                select(Scadenza)
                .where(Scadenza.azienda_id == a.id)
                .order_by(Scadenza.completata, Scadenza.data)
            )
        ),
        "aiuti": aiuti,
        "aggiornamenti": list(
            db.scalars(
                select(AggiornamentoCliente)
                .where(AggiornamentoCliente.azienda_id == a.id)
                .order_by(AggiornamentoCliente.creato_il.desc())
                .limit(20)
            )
        ),
        "plafond": plafond_residuo(aiuti, "generale", a.plafond_de_minimis_manuale),
        "dimensione": dimensione,
        "dimensione_provenienza": provenienza,
        "categorie_spesa": CATEGORIE_SPESA,
        "etichette_progetto": ETICHETTE_PROGETTO,
        "etichette_attivita": ETICHETTE_ATTIVITA,
        "etichette_regolamento": ETICHETTE_REGOLAMENTO,
        "oggi": date.today(),
    }
