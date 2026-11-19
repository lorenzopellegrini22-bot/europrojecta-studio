"""Prefattibilità: prima di aprire una pratica, il cliente può partecipare al bando?

Le voci automatiche confrontano i requisiti noti del bando (bando_requisiti) con i dati del
cliente: territorio, dimensione, ATECO, stato dell'attività, stato del bando, regime di aiuto.
Il consulente può correggerle, aggiungerne di sue e decidere l'esito. È un aiuto alla
valutazione, non un parere: ciò che non è noto resta "da verificare".
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.models import Azienda, Bando, Prefattibilita, Utente
from app.models.pratiche import ESITI_PREFATTIBILITA
from app.utils.numeri import importo_italiano
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

ETICHETTE_ESITO = {
    "da_valutare": "Da valutare",
    "fattibile": "Fattibile",
    "fattibile_con_riserva": "Fattibile con riserva",
    "non_fattibile": "Non fattibile",
}
ESITI_VOCE = ("ok", "ko", "da_verificare")
ETICHETTE_VOCE = {"ok": "Soddisfatto", "ko": "Non soddisfatto", "da_verificare": "Da verificare"}


def _voce(requisito: str, esito: str, nota: str) -> dict[str, Any]:
    return {"requisito": requisito, "esito": esito, "nota": nota, "automatica": True}


def _ateco_compatibile(codice: str, ammessi: list[str]) -> bool:
    c = codice.replace(".", "")
    return any(c.startswith(a.replace(".", "")) for a in ammessi)


def voci_automatiche(azienda: Azienda, bando: Bando | None, oggi: date) -> list[dict[str, Any]]:
    voci: list[dict[str, Any]] = []
    if bando is None:
        return [
            _voce(
                "Requisiti del bando",
                "da_verificare",
                "Il bando non è tra quelli raccolti: requisiti da verificare sul testo del bando.",
            )
        ]
    req = bando.requisiti
    # Stato del bando
    if bando.stato == "chiuso":
        voci.append(_voce("Bando aperto", "ko", "Il bando risulta chiuso."))
    elif bando.stato == "in_arrivo":
        voci.append(
            _voce(
                "Bando aperto", "ok", f"In arrivo: apre il {bando.data_apertura or 'da definire'}."
            )
        )
    elif bando.stato == "aperto":
        nota = (
            f"Aperto, chiude il {bando.data_chiusura:%d/%m/%Y}."
            if bando.data_chiusura
            else "Aperto."
        )
        voci.append(_voce("Bando aperto", "ok", nota))
    else:
        voci.append(_voce("Bando aperto", "da_verificare", "Date del bando non note."))
    # Stato dell'attività
    if azienda.stato_attivita and azienda.stato_attivita.upper() != "ATTIVA":
        voci.append(_voce("Impresa attiva", "ko", f"Registro Imprese: {azienda.stato_attivita}."))
    if req is None:
        voci.append(_voce("Requisiti del bando", "da_verificare", "Requisiti non ancora estratti."))
        return voci
    # Territorio
    from app.opportunita.abbinamento import sedi_cliente

    sede = azienda.sede_legale
    regione = sede.regione if sede else None
    operativa = next(
        (
            s
            for s in sedi_cliente(azienda)
            if s.tipo == "operativa" and s.regione in (req.regioni or [])
        ),
        None,
    )
    if (
        req.territorio in ("regionale", "provinciale", "comunale")
        and req.regioni
        and regione not in req.regioni
        and operativa is not None
    ):
        extra = (
            " Ammessi solo alcuni comuni o province: controllare che la sede vi rientri."
            if req.territorio != "regionale"
            else " Controllare che il bando ammetta le sedi operative (di solito sì)."
        )
        voci.append(
            _voce(
                "Territorio",
                "da_verificare",
                f"Sede operativa a {operativa.comune_testo} ({operativa.regione}).{extra}",
            )
        )
    elif req.territorio == "nazionale":
        voci.append(_voce("Territorio", "ok", "Bando nazionale."))
    elif req.territorio in ("regionale", "provinciale", "comunale") and req.regioni:
        if not regione:
            voci.append(
                _voce(
                    "Territorio",
                    "da_verificare",
                    f"Bando per {', '.join(req.regioni)}: manca la regione della sede del cliente.",
                )
            )
        elif regione in req.regioni:
            extra = (
                " Ammessi solo alcuni comuni o province: controllare la sede operativa."
                if req.territorio != "regionale"
                else ""
            )
            voci.append(
                _voce(
                    "Territorio",
                    "da_verificare" if extra else "ok",
                    f"Sede legale in {regione}.{extra}",
                )
            )
        else:
            voci.append(
                _voce(
                    "Territorio",
                    "ko",
                    f"Bando per {', '.join(req.regioni)}; sede legale in {regione}. Controllare "
                    "se esiste una sede operativa nel territorio.",
                )
            )
    else:
        voci.append(_voce("Territorio", "da_verificare", "Territorio del bando non noto."))
    # Dimensione
    if not req.dimensioni:
        voci.append(_voce("Dimensione d'impresa", "da_verificare", "Vincolo non noto."))
    elif not azienda.dimensione_manuale:
        voci.append(
            _voce(
                "Dimensione d'impresa",
                "da_verificare",
                f"Ammesse: {', '.join(req.dimensioni)}. Dimensione del cliente non indicata.",
            )
        )
    elif azienda.dimensione_manuale in req.dimensioni:
        voci.append(
            _voce("Dimensione d'impresa", "ok", f"{azienda.dimensione_manuale.capitalize()}.")
        )
    else:
        voci.append(
            _voce(
                "Dimensione d'impresa",
                "ko",
                f"Ammesse: {', '.join(req.dimensioni)}; il cliente è {azienda.dimensione_manuale}.",
            )
        )
    # ATECO
    primario = azienda.ateco_primario
    if not req.ateco_ammessi:
        voci.append(_voce("Codice ATECO", "ok", "Nessun vincolo ATECO noto."))
    elif primario is None:
        voci.append(
            _voce(
                "Codice ATECO",
                "da_verificare",
                "Il bando ammette solo alcuni ATECO; manca l'ATECO del cliente.",
            )
        )
    elif _ateco_compatibile(primario.codice, req.ateco_ammessi):
        voci.append(_voce("Codice ATECO", "ok", f"{primario.codice} tra gli ammessi."))
    else:
        voci.append(
            _voce(
                "Codice ATECO",
                "ko",
                f"{primario.codice} non risulta tra gli ammessi (controllare anche i codici "
                "secondari).",
            )
        )
    # Regime di aiuto
    if req.regime == "de_minimis":
        voci.append(
            _voce(
                "Plafond de minimis",
                "da_verificare",
                "Aiuto in de minimis: verificare gli aiuti ricevuti negli ultimi 3 anni (RNA).",
            )
        )
    return voci


def esito_suggerito(voci: list[dict[str, Any]]) -> str:
    esiti = {v.get("esito") for v in voci}
    if "ko" in esiti:
        return "non_fattibile"
    if "da_verificare" in esiti:
        return "fattibile_con_riserva"
    return "fattibile"


def _importo(valore: str | None) -> Decimal | None:
    d = importo_italiano(valore)
    return d if d is not None and 0 <= d < Decimal("1e12") else None


def _carica(db: Session, pid: int) -> Prefattibilita:
    p = db.get(
        Prefattibilita,
        pid,
        options=[
            selectinload(Prefattibilita.azienda).selectinload(Azienda.ateco),
            selectinload(Prefattibilita.azienda).selectinload(Azienda.sedi),
            selectinload(Prefattibilita.bando).selectinload(Bando.requisiti),
        ],
    )
    if p is None:
        raise HTTPException(404)
    return p


@router.get("/prefattibilita")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    elenco = list(
        db.scalars(
            select(Prefattibilita)
            .options(
                selectinload(Prefattibilita.azienda),
                selectinload(Prefattibilita.bando),
                selectinload(Prefattibilita.utente),
            )
            .order_by(Prefattibilita.creato_il.desc())
            .limit(200)
        )
    )
    return render(
        request, "prefattibilita/lista.html", {"elenco": elenco, "etichette": ETICHETTE_ESITO}
    )


@router.get("/prefattibilita/nuova")
def nuova(request: Request, db: Db, _: Autenticato) -> Response:
    p = request.query_params
    azienda_id = int(p["azienda_id"]) if (p.get("azienda_id") or "").isdigit() else None
    bando_id = int(p["bando_id"]) if (p.get("bando_id") or "").isdigit() else None
    return render(
        request,
        "prefattibilita/nuova.html",
        {
            "aziende": list(db.scalars(select(Azienda).order_by(Azienda.ragione_sociale))),
            "azienda_id": azienda_id,
            "bando": db.get(Bando, bando_id) if bando_id else None,
            "errore": None,
        },
    )


@router.post("/prefattibilita/nuova", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    aid = testo(form, "azienda_id")
    bid = testo(form, "bando_id")
    azienda = db.get(Azienda, int(aid)) if aid and aid.isdigit() else None
    bando = db.get(Bando, int(bid)) if bid and bid.isdigit() else None
    bando_testo = testo(form, "bando_testo")
    if azienda is None or (bando is None and not bando_testo):
        return render(
            request,
            "prefattibilita/nuova.html",
            {
                "aziende": list(db.scalars(select(Azienda).order_by(Azienda.ragione_sociale))),
                "azienda_id": azienda.id if azienda else None,
                "bando": bando,
                "errore": "Scegli il cliente e il bando (dall'elenco o scrivendone il nome).",
            },
            400,
        )
    voci = voci_automatiche(azienda, bando, date.today())
    pf = Prefattibilita(
        azienda=azienda,
        bando=bando,
        bando_testo=None if bando else bando_testo,
        voci=voci,
        esito="da_valutare",
        utente_id=utente.id,
    )
    db.add(pf)
    db.flush()
    audit.registra(db, utente, "prefattibilita", pf.id, "crea", {"azienda": azienda.id})
    db.commit()
    return RedirectResponse(f"/prefattibilita/{pf.id}", status_code=303)


@router.get("/prefattibilita/{pid}")
def scheda(request: Request, pid: int, db: Db, _: Autenticato) -> Response:
    pf = _carica(db, pid)
    return render(
        request,
        "prefattibilita/scheda.html",
        {
            "pf": pf,
            "esiti": ESITI_PREFATTIBILITA,
            "etichette": ETICHETTE_ESITO,
            "esiti_voce": ESITI_VOCE,
            "etichette_voce": ETICHETTE_VOCE,
            "suggerito": esito_suggerito(pf.voci),
        },
    )


@router.post("/prefattibilita/{pid}", dependencies=CSRF)
def salva(pid: int, db: Db, utente: Autenticato, form: Form) -> Response:
    pf = _carica(db, pid)
    voci = []
    for i, v in enumerate(pf.voci):
        if form.get(f"elimina_{i}") == "1":
            continue
        esito = testo(form, f"esito_{i}")
        voci.append(
            {
                **v,
                "esito": esito if esito in ESITI_VOCE else v.get("esito"),
                "nota": testo(form, f"nota_{i}") or "",
            }
        )
    nuovo = testo(form, "nuovo_requisito")
    if nuovo:
        esito = testo(form, "nuovo_esito")
        voci.append(
            {
                "requisito": nuovo,
                "esito": esito if esito in ESITI_VOCE else "da_verificare",
                "nota": testo(form, "nuova_nota") or "",
                "automatica": False,
            }
        )
    pf.voci = voci
    esito = testo(form, "esito")
    if esito in ESITI_PREFATTIBILITA:
        pf.esito = esito
    pf.importo_progetto = _importo(testo(form, "importo_progetto"))
    pf.contributo_stimato = _importo(testo(form, "contributo_stimato"))
    pf.note = testo(form, "note")
    audit.registra(db, utente, "prefattibilita", pf.id, "modifica", {"esito": pf.esito})
    db.commit()
    return RedirectResponse(f"/prefattibilita/{pf.id}", status_code=303)


@router.post("/prefattibilita/{pid}/ricalcola", dependencies=CSRF)
def ricalcola(pid: int, db: Db, _: Autenticato) -> Response:
    """Rifà le voci automatiche (dopo aver aggiornato il cliente); le voci manuali restano."""
    pf = _carica(db, pid)
    manuali = [v for v in pf.voci if not v.get("automatica")]
    pf.voci = voci_automatiche(pf.azienda, pf.bando, date.today()) + manuali
    db.commit()
    return RedirectResponse(f"/prefattibilita/{pf.id}", status_code=303)


@router.post("/prefattibilita/{pid}/elimina", dependencies=CSRF)
def elimina(pid: int, db: Db, utente: Titolare) -> Response:
    pf = _carica(db, pid)
    audit.registra(db, utente, "prefattibilita", pf.id, "elimina", None)
    db.delete(pf)
    db.commit()
    return RedirectResponse("/prefattibilita", status_code=303)
