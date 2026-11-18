"""Pratiche a fasi: elenco con filtri ed export, apertura da modello, lavorazione per fasi,
riepilogo stampabile; scadenzario.

Titolare e collaboratori lavorano sulle pratiche; solo il titolare può eliminarle."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.models import (
    Azienda,
    Bando,
    ModelloFase,
    ModelloPratica,
    Pratica,
    PraticaDocumento,
    PraticaFase,
    PraticaOperazione,
    Utente,
)
from app.models.pratiche import (
    STATI_DOCUMENTO,
    STATI_OPERAZIONE,
    STATI_PRATICA,
    STATI_PRATICA_CONCLUSI,
    TIPI_OPERAZIONE,
)
from app.pratiche import servizio as s
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

PER_PAGINA = 30


def _oggi() -> date:
    return date.today()


def _data(valore: str | None) -> date | None:
    try:
        return date.fromisoformat(valore) if valore else None
    except ValueError:
        return None


def _importo(valore: str | None) -> Decimal | None:
    if not valore:
        return None
    pulito = valore.replace("€", "").replace(" ", "")
    if "," in pulito:
        pulito = pulito.replace(".", "").replace(",", ".")
    try:
        d = Decimal(pulito)
    except InvalidOperation:
        return None
    return d if 0 <= d < Decimal("1e12") else None


def _intero(valore: str | None) -> int | None:
    try:
        return int(valore) if valore else None
    except ValueError:
        return None


def _utenti(db: Session) -> list[Utente]:
    return list(db.scalars(select(Utente).where(Utente.attivo).order_by(Utente.nome)))


def _pratica(db: Session, pratica_id: int) -> Pratica:
    p = db.get(
        Pratica,
        pratica_id,
        options=[
            selectinload(Pratica.azienda).selectinload(Azienda.referenti),
            selectinload(Pratica.bando),
            selectinload(Pratica.fasi)
            .selectinload(PraticaFase.operazioni)
            .selectinload(PraticaOperazione.documenti),
            selectinload(Pratica.fasi)
            .selectinload(PraticaFase.operazioni)
            .selectinload(PraticaOperazione.dati),
            selectinload(Pratica.eventi),
        ],
    )
    if p is None:
        raise HTTPException(404)
    return p


def _fase(p: Pratica, fase_id: int) -> PraticaFase:
    f = next((f for f in p.fasi if f.id == fase_id), None)
    if f is None:
        raise HTTPException(404)
    return f


def _operazione(p: Pratica, op_id: int) -> PraticaOperazione:
    op = next((o for f in p.fasi for o in f.operazioni if o.id == op_id), None)
    if op is None:
        raise HTTPException(404)
    return op


def _torna(p: Pratica, fase: PraticaFase | None = None, ancora: str = "") -> Response:
    url = f"/pratiche/{p.id}"
    if fase is not None:
        url += f"?fase={fase.numero}"
    return RedirectResponse(url + (f"#{ancora}" if ancora else ""), status_code=303)


# --- Elenco ed export --------------------------------------------------------------------


def _query_elenco(request: Request) -> tuple[Any, dict[str, Any]]:
    p = request.query_params
    filtri = {
        "q": (p.get("q") or "").strip(),
        "stato": p.get("stato") or "attive",
        "responsabile": _intero(p.get("responsabile")),
        "modello": _intero(p.get("modello")),
    }
    query = (
        select(Pratica)
        .join(Azienda)
        .outerjoin(Bando, Pratica.bando_id == Bando.id)
        .options(
            selectinload(Pratica.azienda),
            selectinload(Pratica.bando),
            selectinload(Pratica.modello),
            selectinload(Pratica.responsabile),
            selectinload(Pratica.fasi).selectinload(PraticaFase.operazioni),
        )
        .order_by(Pratica.creato_il.desc())
    )
    if filtri["q"]:
        m = f"%{filtri['q']}%"
        query = query.where(
            or_(
                Pratica.codice.ilike(m),
                Azienda.ragione_sociale.ilike(m),
                Bando.titolo.ilike(m),
                Pratica.bando_testo.ilike(m),
                Pratica.protocollo.ilike(m),
            )
        )
    if filtri["stato"] == "attive":
        query = query.where(Pratica.stato.not_in(STATI_PRATICA_CONCLUSI))
    elif filtri["stato"] in STATI_PRATICA:
        query = query.where(Pratica.stato == filtri["stato"])
    if filtri["responsabile"]:
        query = query.where(Pratica.responsabile_id == filtri["responsabile"])
    if filtri["modello"]:
        query = query.where(Pratica.modello_id == filtri["modello"])
    return query, filtri


@router.get("/pratiche")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    query, filtri = _query_elenco(request)
    pratiche = list(db.scalars(query))
    pagina = max(_intero(request.query_params.get("pagina")) or 1, 1)
    totale = len(pratiche)
    pratiche = pratiche[(pagina - 1) * PER_PAGINA : pagina * PER_PAGINA]
    return render(
        request,
        "pratiche/lista.html",
        {
            "pratiche": pratiche,
            "totale": totale,
            "pagina": pagina,
            "pagine": max((totale + PER_PAGINA - 1) // PER_PAGINA, 1),
            "filtri": filtri,
            "utenti": _utenti(db),
            "modelli": list(db.scalars(select(ModelloPratica).order_by(ModelloPratica.nome))),
            "etichette_stato": s.ETICHETTE_STATO_PRATICA,
            "avanzamento": s.avanzamento,
            "oggi": _oggi(),
        },
    )


@router.get("/pratiche/export.csv")
def esporta(request: Request, db: Db, utente: Autenticato) -> Response:
    """CSV per Excel: separatore ";" e BOM UTF-8, così le lettere accentate restano corrette."""
    query, _ = _query_elenco(request)
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";")
    w.writerow(
        [
            "Codice",
            "Cliente",
            "Partita IVA",
            "Bando",
            "Modello",
            "Fase in corso",
            "Stato",
            "Responsabile",
            "Importo richiesto",
            "Importo concesso",
            "Protocollo",
            "Scadenza presentazione",
            "Operazioni chiuse",
            "Aperta il",
        ]
    )

    def euro(v: Decimal | None) -> str:
        return f"{v:.2f}".replace(".", ",") if v is not None else ""

    for p in db.scalars(query):
        fatte, tot = s.avanzamento(p)
        fase = p.fase_attiva
        w.writerow(
            [
                p.codice,
                p.azienda.ragione_sociale,
                p.azienda.partita_iva or "",
                p.nome_bando,
                p.modello.nome if p.modello else "",
                f"{fase.numero}. {fase.nome}" if fase else "",
                s.ETICHETTE_STATO_PRATICA[p.stato],
                p.responsabile.nome if p.responsabile else "",
                euro(p.importo_richiesto),
                euro(p.importo_concesso),
                p.protocollo or "",
                p.scadenza_presentazione.strftime("%d/%m/%Y") if p.scadenza_presentazione else "",
                f"{fatte}/{tot}",
                p.creato_il.strftime("%d/%m/%Y"),
            ]
        )
    audit.registra(db, utente, "pratica", None, "esporta", {"formato": "csv"})
    db.commit()
    return Response(
        "﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pratiche.csv"'},
    )


# --- Nuova pratica -----------------------------------------------------------------------


@router.get("/pratiche/cerca-bandi")
def cerca_bandi(request: Request, db: Db, _: Autenticato) -> Response:
    """Frammento HTMX: i bandi che corrispondono alla ricerca, aperti e in arrivo per primi."""
    q = (request.query_params.get("q") or "").strip()
    bandi: list[Bando] = []
    if len(q) >= 3:
        bandi = list(
            db.scalars(
                select(Bando)
                .where(or_(Bando.titolo.ilike(f"%{q}%"), Bando.ente.ilike(f"%{q}%")))
                .order_by(Bando.stato.in_(("aperto", "in_arrivo")).desc(), Bando.titolo)
                .limit(20)
            )
        )
    return render(request, "pratiche/_risultati_bandi.html", {"bandi": bandi, "q": q})


def _form_nuova(
    request: Request, db: Session, valori: dict[str, Any], errori: dict[str, str]
) -> Response:
    bando = db.get(Bando, valori["bando_id"]) if valori.get("bando_id") else None
    return render(
        request,
        "pratiche/nuova.html",
        {
            "v": valori,
            "errori": errori,
            "bando": bando,
            "aziende": list(db.scalars(select(Azienda).order_by(Azienda.ragione_sociale))),
            "modelli": list(
                db.scalars(
                    select(ModelloPratica)
                    .where(ModelloPratica.attivo)
                    .options(selectinload(ModelloPratica.fasi))
                    .order_by(ModelloPratica.nome)
                )
            ),
            "utenti": _utenti(db),
        },
        400 if errori else 200,
    )


@router.get("/pratiche/nuova")
def nuova(request: Request, db: Db, utente: Autenticato) -> Response:
    p = request.query_params
    return _form_nuova(
        request,
        db,
        {
            "azienda_id": _intero(p.get("azienda_id")),
            "bando_id": _intero(p.get("bando_id")),
            "responsabile_id": utente.id,
        },
        {},
    )


@router.post("/pratiche/nuova", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    v = {
        "azienda_id": _intero(testo(form, "azienda_id")),
        "bando_id": _intero(testo(form, "bando_id")),
        "bando_testo": testo(form, "bando_testo"),
        "modello_id": _intero(testo(form, "modello_id")),
        "responsabile_id": _intero(testo(form, "responsabile_id")),
        "scadenza_presentazione": testo(form, "scadenza_presentazione"),
    }
    errori: dict[str, str] = {}
    azienda = db.get(Azienda, v["azienda_id"]) if v["azienda_id"] else None
    if azienda is None:
        errori["azienda_id"] = "Scegli il cliente."
    bando = db.get(Bando, v["bando_id"]) if v["bando_id"] else None
    if bando is None and not v["bando_testo"]:
        errori["bando"] = "Scegli un bando dall'elenco oppure scrivine il nome."
    modello = db.get(ModelloPratica, v["modello_id"]) if v["modello_id"] else None
    scadenza = _data(v["scadenza_presentazione"])
    if v["scadenza_presentazione"] and scadenza is None:
        errori["scadenza_presentazione"] = "Data non valida."
    if errori:
        return _form_nuova(request, db, v, errori)
    responsabile = db.get(Utente, v["responsabile_id"]) if v["responsabile_id"] else None
    pratica = s.crea_pratica(
        db,
        utente,
        azienda,  # type: ignore[arg-type]
        _oggi(),
        bando=bando,
        bando_testo=v["bando_testo"],
        modello=modello,
        responsabile=responsabile,
        scadenza_presentazione=scadenza,
    )
    db.flush()
    audit.registra(db, utente, "pratica", pratica.id, "crea", {"codice": pratica.codice})
    db.commit()
    return RedirectResponse(f"/pratiche/{pratica.id}", status_code=303)


# --- Lavorazione -------------------------------------------------------------------------


@router.get("/pratiche/{pratica_id}")
def scheda(request: Request, pratica_id: int, db: Db, _: Autenticato) -> Response:
    p = _pratica(db, pratica_id)
    numero = _intero(request.query_params.get("fase"))
    fase = next((f for f in p.fasi if f.numero == numero), None) or p.fase_attiva or p.fasi[0]
    valori = s.valori_segnaposto(p)
    email = {
        op.id: (s.componi(op.oggetto_email, valori), s.componi(op.testo_email, valori))
        for op in fase.operazioni
        if op.tipo == "comunicazione"
    }
    return render(
        request,
        "pratiche/scheda.html",
        {
            "p": p,
            "fase": fase,
            "email": email,
            "referente": p.azienda.referente_principale,
            "utenti": _utenti(db),
            "avanzamento": s.avanzamento(p),
            "stati_pratica": STATI_PRATICA,
            "etichette_stato": s.ETICHETTE_STATO_PRATICA,
            "etichette_tipo": s.ETICHETTE_TIPO_OPERAZIONE,
            "etichette_documento": s.ETICHETTE_STATO_DOCUMENTO,
            "stati_documento": STATI_DOCUMENTO,
            "tipi_operazione": TIPI_OPERAZIONE,
            "oggi": _oggi(),
        },
    )


@router.get("/pratiche/{pratica_id}/riepilogo")
def riepilogo(request: Request, pratica_id: int, db: Db, _: Autenticato) -> Response:
    p = _pratica(db, pratica_id)
    return render(
        request,
        "pratiche/riepilogo.html",
        {
            "p": p,
            "avanzamento": s.avanzamento(p),
            "etichette_stato": s.ETICHETTE_STATO_PRATICA,
            "etichette_tipo": s.ETICHETTE_TIPO_OPERAZIONE,
            "etichette_documento": s.ETICHETTE_STATO_DOCUMENTO,
            "oggi": _oggi(),
        },
    )


@router.post("/pratiche/{pratica_id}/dati", dependencies=CSRF)
def salva_dati(pratica_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    p = _pratica(db, pratica_id)
    nuovo_stato = testo(form, "stato")
    if nuovo_stato in STATI_PRATICA:
        s.cambia_stato(p, nuovo_stato, utente, testo(form, "nota_stato"))
    differenze: dict[str, Any] = {}
    nuovi = {
        "responsabile_id": _intero(testo(form, "responsabile_id")),
        "importo_richiesto": _importo(testo(form, "importo_richiesto")),
        "importo_concesso": _importo(testo(form, "importo_concesso")),
        "protocollo": testo(form, "protocollo"),
        "scadenza_presentazione": _data(testo(form, "scadenza_presentazione")),
        "note": testo(form, "note"),
    }
    for campo, valore in nuovi.items():
        if getattr(p, campo) != valore:
            setattr(p, campo, valore)
            differenze[campo] = str(valore) if valore is not None else None
    if differenze or nuovo_stato:
        audit.registra(db, utente, "pratica", p.id, "modifica", {**differenze, "stato": p.stato})
    db.commit()
    return _torna(p, ancora="dati-pratica")


@router.post("/pratiche/{pratica_id}/fasi/{fase_id}/azione/{azione}", dependencies=CSRF)
def azione_fase(
    pratica_id: int, fase_id: int, azione: str, db: Db, utente: Autenticato
) -> Response:
    p = _pratica(db, pratica_id)
    fase = _fase(p, fase_id)
    if azione == "completa":
        s.completa_fase(p, fase, utente, _oggi())
        destinazione = p.fase_attiva or fase
    elif azione == "salta":
        s.salta_fase(p, fase, utente, _oggi())
        destinazione = p.fase_attiva or fase
    elif azione == "attiva":
        s.attiva_fase(p, fase, utente, _oggi())
        destinazione = fase
    else:
        raise HTTPException(404)
    db.commit()
    return _torna(p, destinazione)


@router.post("/pratiche/{pratica_id}/fasi/{fase_id}/operazioni", dependencies=CSRF)
def aggiungi_operazione(
    pratica_id: int, fase_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    p = _pratica(db, pratica_id)
    fase = _fase(p, fase_id)
    titolo = testo(form, "titolo")
    tipo = testo(form, "tipo") or "attivita"
    if titolo and tipo in TIPI_OPERAZIONE:
        op = PraticaOperazione(
            numero=len(fase.operazioni) + 1,
            titolo=titolo,
            tipo=tipo,
            stato="da_fare",
            scadenza=_data(testo(form, "scadenza")),
        )
        fase.operazioni.append(op)
        s.evento(p, utente, f"Operazione «{titolo}» aggiunta alla fase {fase.numero}")
        db.commit()
        return _torna(p, fase, f"op-{op.id}")
    return _torna(p, fase)


@router.post("/pratiche/{pratica_id}/operazioni/{op_id}/stato", dependencies=CSRF)
def stato_operazione(
    pratica_id: int, op_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    p = _pratica(db, pratica_id)
    op = _operazione(p, op_id)
    stato = testo(form, "stato")
    if stato in STATI_OPERAZIONE:
        s.imposta_stato_operazione(p, op, stato, utente)
        db.commit()
    return _torna(p, op.fase, f"op-{op.id}")


@router.post("/pratiche/{pratica_id}/operazioni/{op_id}", dependencies=CSRF)
def salva_operazione(
    pratica_id: int, op_id: int, db: Db, utente: Autenticato, form: Form
) -> Response:
    """Salva scadenza, assegnatario e note; stati dei documenti; valori dei dati; testo email."""
    p = _pratica(db, pratica_id)
    op = _operazione(p, op_id)
    op.scadenza = _data(testo(form, "scadenza"))
    op.assegnata_a = _intero(testo(form, "assegnata_a"))
    op.note = testo(form, "note")
    for d in op.documenti:
        stato = testo(form, f"documento_{d.id}")
        if stato in STATI_DOCUMENTO:
            d.stato = stato
        d.nota = testo(form, f"documento_nota_{d.id}")
    for dato in op.dati:
        dato.valore = testo(form, f"dato_{dato.id}")
    if op.tipo == "comunicazione":
        op.oggetto_email = testo(form, "oggetto_email")
        op.testo_email = testo(form, "testo_email")
    nuovo_documento = testo(form, "nuovo_documento")
    if nuovo_documento:
        op.documenti.append(PraticaDocumento(nome=nuovo_documento, stato="mancante"))
    db.commit()
    return _torna(p, op.fase, f"op-{op.id}")


@router.post("/pratiche/{pratica_id}/operazioni/{op_id}/elimina", dependencies=CSRF)
def elimina_operazione(pratica_id: int, op_id: int, db: Db, utente: Autenticato) -> Response:
    p = _pratica(db, pratica_id)
    op = _operazione(p, op_id)
    fase = op.fase
    fase.operazioni.remove(op)
    s.rinumera(fase.operazioni)
    s.evento(p, utente, f"Operazione «{op.titolo}» eliminata dalla fase {fase.numero}")
    db.commit()
    return _torna(p, fase)


@router.post("/pratiche/{pratica_id}/elimina", dependencies=CSRF)
def elimina(pratica_id: int, db: Db, utente: Titolare) -> Response:
    p = _pratica(db, pratica_id)
    audit.registra(db, utente, "pratica", p.id, "elimina", {"codice": p.codice})
    db.delete(p)
    db.commit()
    return RedirectResponse("/pratiche", status_code=303)


# --- Scadenzario -------------------------------------------------------------------------


@router.get("/scadenzario")
def scadenzario(request: Request, db: Db, utente: Autenticato) -> Response:
    oggi = _oggi()
    q = request.query_params
    dal = _data(q.get("dal")) or oggi - timedelta(days=30)
    al = _data(q.get("al")) or oggi + timedelta(days=60)
    mie = q.get("mie") == "1"
    voci = s.scadenze(db, dal, al, utente.id if mie else None)
    giorni: dict[date, list[s.VoceScadenza]] = {}
    for v in voci:
        giorni.setdefault(v.data, []).append(v)
    return render(
        request,
        "pratiche/scadenzario.html",
        {
            "giorni": giorni,
            "totale": len(voci),
            "dal": dal,
            "al": al,
            "mie": mie,
            "oggi": oggi,
        },
    )


# --- Modelli di pratica ------------------------------------------------------------------


def _modello(db: Session, modello_id: int) -> ModelloPratica:
    m = db.get(
        ModelloPratica,
        modello_id,
        options=[selectinload(ModelloPratica.fasi).selectinload(ModelloFase.operazioni)],
    )
    if m is None:
        raise HTTPException(404)
    return m


def _fase_modello(m: ModelloPratica, fase_id: int) -> ModelloFase:
    f = next((f for f in m.fasi if f.id == fase_id), None)
    if f is None:
        raise HTTPException(404)
    return f


@router.get("/modelli")
def modelli(request: Request, db: Db, _: Autenticato) -> Response:
    elenco = list(
        db.scalars(
            select(ModelloPratica)
            .options(selectinload(ModelloPratica.fasi).selectinload(ModelloFase.operazioni))
            .order_by(ModelloPratica.nome)
        )
    )
    uso = dict(
        db.execute(
            select(Pratica.modello_id, func.count())
            .where(Pratica.modello_id.is_not(None))
            .group_by(Pratica.modello_id)
        ).all()
    )
    return render(request, "modelli/lista.html", {"modelli": elenco, "uso": uso})


@router.post("/modelli/esempio", dependencies=CSRF)
def carica_esempi(db: Db, utente: Titolare) -> Response:
    from app.pratiche.modelli_esempio import crea_modelli_esempio

    creati = crea_modelli_esempio(db)
    if creati:
        audit.registra(db, utente, "modello_pratica", None, "crea", {"esempi": creati})
    db.commit()
    return RedirectResponse("/modelli", status_code=303)


@router.post("/modelli/nuovo", dependencies=CSRF)
def nuovo_modello(request: Request, db: Db, utente: Titolare, form: Form) -> Response:
    nome = testo(form, "nome")
    if not nome or db.scalar(select(ModelloPratica).where(ModelloPratica.nome == nome)):
        return render(
            request,
            "errore.html",
            {"messaggio": "Serve un nome, diverso da quello degli altri modelli."},
            400,
        )
    m = ModelloPratica(nome=nome, descrizione=testo(form, "descrizione"))
    m.fasi.append(ModelloFase(numero=1, nome="Prima fase"))
    db.add(m)
    db.flush()
    audit.registra(db, utente, "modello_pratica", m.id, "crea", {"nome": nome})
    db.commit()
    return RedirectResponse(f"/modelli/{m.id}", status_code=303)


@router.get("/modelli/{modello_id}")
def modello(request: Request, modello_id: int, db: Db, _: Autenticato) -> Response:
    return render(
        request,
        "modelli/scheda.html",
        {
            "m": _modello(db, modello_id),
            "tipi": TIPI_OPERAZIONE,
            "etichette_tipo": s.ETICHETTE_TIPO_OPERAZIONE,
            "segnaposto": s.SEGNAPOSTO,
        },
    )


@router.post("/modelli/{modello_id}", dependencies=CSRF)
def salva_modello(
    request: Request, modello_id: int, db: Db, utente: Titolare, form: Form
) -> Response:
    m = _modello(db, modello_id)
    nome = testo(form, "nome")
    if nome and nome != m.nome:
        if db.scalar(select(ModelloPratica).where(ModelloPratica.nome == nome)):
            return render(
                request, "errore.html", {"messaggio": "Esiste già un modello con quel nome."}, 400
            )
        m.nome = nome
    m.descrizione = testo(form, "descrizione")
    m.attivo = form.get("attivo") == "1"
    audit.registra(db, utente, "modello_pratica", m.id, "modifica", {"nome": m.nome})
    db.commit()
    return RedirectResponse(f"/modelli/{m.id}", status_code=303)


@router.post("/modelli/{modello_id}/duplica", dependencies=CSRF)
def duplica(modello_id: int, db: Db, utente: Titolare) -> Response:
    m = _modello(db, modello_id)
    nome = f"{m.nome} (copia)"
    n = 2
    while db.scalar(select(ModelloPratica).where(ModelloPratica.nome == nome)):
        nome = f"{m.nome} (copia {n})"
        n += 1
    copia = s.duplica_modello(db, m, nome)
    db.flush()
    audit.registra(db, utente, "modello_pratica", copia.id, "crea", {"duplicato_da": m.id})
    db.commit()
    return RedirectResponse(f"/modelli/{copia.id}", status_code=303)


@router.post("/modelli/{modello_id}/elimina", dependencies=CSRF)
def elimina_modello(modello_id: int, db: Db, utente: Titolare) -> Response:
    """Le pratiche già aperte restano intatte: hanno la loro copia di fasi e operazioni."""
    m = _modello(db, modello_id)
    audit.registra(db, utente, "modello_pratica", m.id, "elimina", {"nome": m.nome})
    db.delete(m)
    db.commit()
    return RedirectResponse("/modelli", status_code=303)


@router.post("/modelli/{modello_id}/fasi", dependencies=CSRF)
def aggiungi_fase(modello_id: int, db: Db, _: Titolare, form: Form) -> Response:
    m = _modello(db, modello_id)
    nome = testo(form, "nome")
    if nome:
        f = ModelloFase(numero=len(m.fasi) + 1, nome=nome, descrizione=testo(form, "descrizione"))
        m.fasi.append(f)
        db.commit()
        return RedirectResponse(f"/modelli/{m.id}#fase-{f.id}", status_code=303)
    return RedirectResponse(f"/modelli/{m.id}", status_code=303)


@router.post("/modelli/{modello_id}/fasi/{fase_id}", dependencies=CSRF)
def salva_fase(modello_id: int, fase_id: int, db: Db, _: Titolare, form: Form) -> Response:
    m = _modello(db, modello_id)
    f = _fase_modello(m, fase_id)
    azione = testo(form, "azione") or "salva"
    if azione == "elimina":
        m.fasi.remove(f)
        s.rinumera(m.fasi)
    elif azione in ("su", "giu"):
        s.sposta(m.fasi, f, -1 if azione == "su" else 1)
    else:
        f.nome = testo(form, "nome") or f.nome
        f.descrizione = testo(form, "descrizione")
    db.commit()
    return RedirectResponse(f"/modelli/{m.id}#fase-{fase_id}", status_code=303)


def _campi_operazione(form: dict[str, Any]) -> dict[str, Any]:
    tipo = testo(form, "tipo") or "attivita"
    giorni = _intero(testo(form, "giorni_previsti"))
    return {
        "titolo": testo(form, "titolo"),
        "descrizione": testo(form, "descrizione"),
        "tipo": tipo if tipo in TIPI_OPERAZIONE else "attivita",
        "giorni_previsti": giorni if giorni is not None and giorni >= 0 else None,
        "documenti": s.righe(testo(form, "documenti")) if tipo == "documenti" else None,
        "campi": s.righe(testo(form, "campi")) if tipo == "dati" else None,
        "oggetto_email": testo(form, "oggetto_email") if tipo == "comunicazione" else None,
        "testo_email": testo(form, "testo_email") if tipo == "comunicazione" else None,
    }


@router.post("/modelli/{modello_id}/fasi/{fase_id}/operazioni", dependencies=CSRF)
def aggiungi_operazione_modello(
    modello_id: int, fase_id: int, db: Db, _: Titolare, form: Form
) -> Response:
    from app.models import ModelloOperazione

    m = _modello(db, modello_id)
    f = _fase_modello(m, fase_id)
    campi = _campi_operazione(form)
    if campi["titolo"]:
        f.operazioni.append(ModelloOperazione(numero=len(f.operazioni) + 1, **campi))
        db.commit()
    return RedirectResponse(f"/modelli/{m.id}#fase-{f.id}", status_code=303)


@router.post("/modelli/{modello_id}/operazioni/{op_id}", dependencies=CSRF)
def salva_operazione_modello(
    modello_id: int, op_id: int, db: Db, _: Titolare, form: Form
) -> Response:
    m = _modello(db, modello_id)
    op = next((o for f in m.fasi for o in f.operazioni if o.id == op_id), None)
    if op is None:
        raise HTTPException(404)
    fase = op.fase
    azione = testo(form, "azione") or "salva"
    if azione == "elimina":
        fase.operazioni.remove(op)
        s.rinumera(fase.operazioni)
    elif azione in ("su", "giu"):
        s.sposta(fase.operazioni, op, -1 if azione == "su" else 1)
    else:
        campi = _campi_operazione(form)
        if not campi["titolo"]:
            campi["titolo"] = op.titolo
        for k, v in campi.items():
            setattr(op, k, v)
    db.commit()
    return RedirectResponse(f"/modelli/{m.id}#fase-{fase.id}", status_code=303)
