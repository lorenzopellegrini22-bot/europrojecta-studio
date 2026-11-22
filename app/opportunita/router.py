"""Pagina Opportunità: bandi e appalti adatti ai clienti, con le azioni del consulente."""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import utente_corrente, verifica_csrf
from app.comunicazioni import email_disponibile, invia_registrando
from app.db import get_db
from app.models import Azienda, Utente
from app.models.opportunita import FEEDBACK, STATI_OPPORTUNITA, Opportunita
from app.opportunita.abbinamento import abbina
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

ETICHETTE_STATO = {
    "nuova": "Nuova",
    "vista": "Vista",
    "interessante": "Interessante",
    "scartata": "Scartata",
    "in_pratica": "Pratica aperta",
}
PER_PAGINA = 100
FILTRI_STATO = {
    "da_valutare": ("nuova", "vista", "interessante"),
    "nuova": ("nuova",),
    "interessante": ("interessante",),
    "scartata": ("scartata",),
    "in_pratica": ("in_pratica",),
    "tutte": STATI_OPPORTUNITA,
}


def _interno(torna: str) -> str:
    """Solo indirizzi di questa app: «//sito.it» porterebbe a un altro sito."""
    if torna.startswith("/") and not torna.startswith(("//", "/\\")):
        return torna
    return "/opportunita"


def _opportunita(db: Session, oid: int) -> Opportunita:
    o = db.get(
        Opportunita,
        oid,
        options=[
            selectinload(Opportunita.azienda).selectinload(Azienda.referenti),
            selectinload(Opportunita.bando),
            selectinload(Opportunita.appalto),
        ],
    )
    if o is None:
        raise HTTPException(404)
    return o


from app.squadra.note import conteggi as conteggi_note  # noqa: E402


def _documentazione(db: Session, elenco: list[Opportunita]) -> dict[int, list[dict]]:
    """Documentazione di ogni opportunità (bando o gara), con una sola query per i PDF."""
    from app.documenti.documentazione import voci_bando, voci_gara
    from app.models.documenti import DocumentoBando

    ids = {o.bando_id for o in elenco if o.bando_id}
    pdf: dict[int, list[DocumentoBando]] = {}
    if ids:
        for d in db.scalars(
            select(DocumentoBando)
            .where(DocumentoBando.bando_id.in_(ids))
            .order_by(DocumentoBando.id)
        ):
            pdf.setdefault(d.bando_id, []).append(d)
    return {
        o.id: voci_bando(o.bando, pdf.get(o.bando_id, []))
        if o.bando
        else voci_gara(o.appalto)
        if o.appalto
        else []
        for o in elenco
    }


@router.get("/opportunita")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    p = request.query_params
    stato = p.get("stato") or "da_valutare"
    tipo = p.get("tipo") or ""
    cliente = int(p["cliente"]) if (p.get("cliente") or "").isdigit() else None
    query = (
        select(Opportunita)
        .where(Opportunita.stato.in_(FILTRI_STATO.get(stato, FILTRI_STATO["da_valutare"])))
        .options(
            selectinload(Opportunita.azienda),
            selectinload(Opportunita.bando),
            selectinload(Opportunita.appalto),
        )
        .order_by(Opportunita.azienda_id, Opportunita.punteggio.desc())
    )
    if tipo == "bando":
        query = query.where(Opportunita.bando_id.is_not(None))
    elif tipo == "appalto":
        query = query.where(Opportunita.appalto_id.is_not(None))
    if cliente:
        query = query.where(Opportunita.azienda_id == cliente)
    # Pagine da PER_PAGINA: con 355 opportunità la pagina pesava 1 MB (22/11/2026), troppo
    # per il telefono. La documentazione si carica solo quando si apre la tendina.
    totale = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    pagine = max(1, -(-totale // PER_PAGINA))
    pagina = min(max(1, int(p["pagina"]) if (p.get("pagina") or "").isdigit() else 1), pagine)
    elenco = list(db.scalars(query.offset((pagina - 1) * PER_PAGINA).limit(PER_PAGINA)))
    per_cliente: dict[int, list[Opportunita]] = {}
    for o in elenco:
        per_cliente.setdefault(o.azienda_id, []).append(o)
    documentazione = _documentazione(db, elenco)
    senza_profilo = db.scalar(
        select(func.count())
        .select_from(Azienda)
        .where(
            Azienda.avvisi_attivi,
            Azienda.soa_possedute.is_(None),
            Azienda.cpv_interesse.is_(None),
        )
    )
    return render(
        request,
        "opportunita/lista.html",
        {
            "per_cliente": per_cliente,
            "documentazione": documentazione,
            "note_per": conteggi_note(db, "opportunita", [o.id for o in elenco]),
            "totale": totale,
            "pagina": pagina,
            "pagine": pagine,
            "filtri": {"stato": stato, "tipo": tipo, "cliente": cliente},
            "filtri_url": urlencode(
                {k: v for k, v in (("stato", stato), ("tipo", tipo), ("cliente", cliente)) if v}
            ),
            "clienti": list(db.scalars(select(Azienda).order_by(Azienda.ragione_sociale))),
            "etichette": ETICHETTE_STATO,
            "senza_profilo": senza_profilo,
            "email_disponibile": email_disponibile(db),
            "messaggio": p.get("msg"),
            "esito": p.get("esito"),
        },
    )


@router.get("/opportunita/{oid}/documentazione")
def documentazione_opportunita(oid: int, request: Request, db: Db, _: Autenticato) -> Response:
    """Frammento HTMX: la documentazione di un'opportunità, caricata all'apertura."""
    o = _opportunita(db, oid)
    return render(
        request,
        "opportunita/_documentazione.html",
        {"docs": _documentazione(db, [o]).get(o.id, [])},
    )


@router.post("/opportunita/aggiorna", dependencies=CSRF)
def aggiorna(db: Db, _: Autenticato) -> Response:
    esito = abbina(db)
    return RedirectResponse(f"/opportunita?esito=ok&msg={quote(esito)}", status_code=303)


@router.post("/opportunita/{oid}/stato", dependencies=CSRF)
def cambia_stato(oid: int, db: Db, utente: Autenticato, form: Form) -> Response:
    o = _opportunita(db, oid)
    stato = testo(form, "stato")
    if stato in STATI_OPPORTUNITA:
        o.stato = stato
        if "nota" in form:
            o.nota = testo(form, "nota")
        audit.registra(db, utente, "opportunita", o.id, "modifica", {"stato": stato})
        db.commit()
    torna = testo(form, "torna") or "/opportunita"
    return RedirectResponse(_interno(torna), status_code=303)


@router.post("/opportunita/{oid}/feedback", dependencies=CSRF)
def dai_feedback(oid: int, db: Db, utente: Autenticato, form: Form) -> Response:
    """Utile / non pertinente / già noto: serve a misurare la precisione del punteggio.
    Non pertinente e già noto tolgono l'opportunità dalla lista e non la ripropongono."""
    o = _opportunita(db, oid)
    feedback = testo(form, "feedback")
    if feedback in FEEDBACK:
        o.feedback = feedback
        o.feedback_il = datetime.now(UTC)
        o.feedback_utente_id = utente.id
        if feedback == "utile" and o.stato in ("nuova", "vista"):
            o.stato = "interessante"
        elif feedback != "utile" and o.stato != "in_pratica":
            o.stato = "scartata"
        audit.registra(db, utente, "opportunita", o.id, "modifica", {"feedback": feedback})
        db.commit()
    torna = testo(form, "torna") or "/opportunita"
    return RedirectResponse(_interno(torna), status_code=303)


def testo_per_cliente(o: Opportunita, mittente: Utente) -> tuple[str, str]:
    ref = o.azienda.referente_principale
    saluto = f"Gentile {ref.nome_completo}," if ref else "Buongiorno,"
    tipo = "un bando" if o.bando_id else "una gara d'appalto"
    righe = [
        saluto,
        "",
        f"abbiamo individuato {tipo} che potrebbe interessare {o.azienda.ragione_sociale}:",
        "",
        f"«{o.titolo}»",
    ]
    if o.scadenza:
        righe.append(f"Scadenza: {o.scadenza:%d/%m/%Y}")
    link = (o.bando.link_ufficiale if o.bando else None) or (o.appalto.link if o.appalto else None)
    if link:
        righe.append(f"Dettagli: {link}")
    motivi = (o.motivi or {}).get("soddisfatti") or []
    if motivi:
        righe += ["", "Perché ve lo segnaliamo:"] + [f"- {m}" for m in motivi[:4]]
    righe += [
        "",
        "Se vi interessa, possiamo verificare insieme i requisiti e preparare la domanda.",
        "",
        "Cordiali saluti,",
        mittente.nome,
    ]
    return f"Opportunità: {o.titolo[:90]}", "\n".join(righe)


@router.post("/opportunita/{oid}/segnala", dependencies=CSRF)
def segnala_al_cliente(oid: int, db: Db, utente: Autenticato) -> Response:
    """Invia al referente principale del cliente un'email con l'opportunità."""
    o = _opportunita(db, oid)
    ref = o.azienda.referente_principale
    if not ref or not ref.email:
        return RedirectResponse(
            "/opportunita?esito=errore&msg="
            + quote("Il cliente non ha un referente con email: aggiungilo nella scheda."),
            status_code=303,
        )
    oggetto, corpo = testo_per_cliente(o, utente)
    ok, messaggio = invia_registrando(
        db,
        tipo="opportunita",
        destinatario=ref.email,
        oggetto=oggetto,
        testo=corpo,
        utente=utente,
        rispondi_a=utente.email,
    )
    if ok:
        o = _opportunita(db, oid)
        o.stato = "interessante"
        audit.registra(db, utente, "opportunita", o.id, "invia", {"destinatario": ref.email})
        db.commit()
    return RedirectResponse(
        f"/opportunita?esito={'ok' if ok else 'errore'}&msg={quote(messaggio)}", status_code=303
    )


@router.post("/opportunita/{oid}/pratica", dependencies=CSRF)
def apri_pratica(oid: int, db: Db, _: Autenticato) -> Response:
    """Segna l'opportunità come "pratica aperta" e porta al form della nuova pratica."""
    o = _opportunita(db, oid)
    o.stato = "in_pratica"
    db.commit()
    if o.bando_id:
        url = f"/pratiche/nuova?azienda_id={o.azienda_id}&bando_id={o.bando_id}"
    else:
        a = o.appalto
        nome = (f"CIG {a.cig} – " if a.cig else "") + a.titolo
        url = f"/pratiche/nuova?azienda_id={o.azienda_id}&bando_testo={quote(nome[:300])}"
        if a.scadenza:
            url += f"&scadenza={a.scadenza.isoformat()}"
    return RedirectResponse(url, status_code=303)
