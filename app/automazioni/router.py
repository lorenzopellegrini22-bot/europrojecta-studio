"""Pagine della sezione Automazioni. Tutti quelli con la sezione «Aggiornamenti» le vedono;
crearle, cambiarle, accenderle ed eseguirle spetta al titolare."""

from datetime import UTC, date, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.aggiornamenti import servizio as agg
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.automazioni import servizio as s
from app.config import get_settings
from app.db import get_db
from app.models import AggiornamentoCliente, Automazione, Azienda, Utente
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]
ETICHETTE_STATO = {
    "bozza": "Bozza da approvare",
    "approvato": "Approvato",
    "inviato": "Inviato",
    "errore": "Non inviato",
    "scartato": "Scartato",
}


def _au(db: Session, au_id: int) -> Automazione:
    au = db.get(Automazione, au_id)
    if au is None:
        raise HTTPException(404)
    return au


def _lista(form: dict[str, Any], campo: str) -> list[str]:
    v = form.get(campo) or []
    return [v] if isinstance(v, str) else list(v)


def _intero(form: dict[str, Any], campo: str) -> int | None:
    v = testo(form, campo)
    return int(v) if v and v.isdigit() else None


def _descrivi(au: Automazione) -> str:
    if au.evento == "scadenza":
        return f"Un bando adatto scade entro {au.giorni_scadenza or 15} giorni"
    if au.evento == "riepilogo":
        return f"Riepilogo {'settimanale' if au.frequenza == 'settimanale' else 'mensile'}"
    return s.EVENTI["nuovo_bando"]


def _contesto_form(db: Session, au: Automazione) -> dict[str, Any]:
    clienti = list(
        db.scalars(
            select(Azienda).where(Azienda.stato_cliente != "ex").order_by(Azienda.ragione_sociale)
        )
    )
    return {
        "au": au,
        "clienti": clienti,
        "eventi": s.EVENTI,
        "destinatari": s.DESTINATARI,
        "azioni": s.AZIONI,
        "canali": s.CANALI,
        "ambiti": s.AMBITI,
        "segnaposto": s.SEGNAPOSTO,
        "modelli": {
            p["evento"]: {"oggetto": p["oggetto"], "testo": p["testo"]} for p in s.PREDEFINITE
        }
        | {"riepilogo": s.TESTO_RIEPILOGO},
    }


def _leggi(au: Automazione, form: dict[str, Any]) -> list[str]:
    """Copia i campi del modulo nell'automazione; restituisce gli errori."""
    errori = []
    au.nome = (testo(form, "nome") or "")[:200]
    if not au.nome:
        errori.append("Dai un nome all'automazione.")
    evento = testo(form, "evento") or ""
    if evento not in s.EVENTI:
        errori.append("Scegli quando deve partire.")
    else:
        au.evento = evento
    giorni = _intero(form, "giorni_scadenza")
    au.giorni_scadenza = min(max(giorni or 15, 1), 120) if au.evento == "scadenza" else None
    frequenza = testo(form, "frequenza")
    au.frequenza = (
        (frequenza if frequenza in s.FREQUENZE else "mensile") if au.evento == "riepilogo" else None
    )
    destinatari = testo(form, "destinatari") or "tutti"
    au.destinatari = destinatari if destinatari in s.DESTINATARI else "tutti"
    ids = [int(x) for x in _lista(form, "aziende_ids") if str(x).isdigit()]
    au.aziende_ids = ids or None
    if au.destinatari == "scelti" and not ids:
        errori.append("Scegli almeno un cliente, oppure «Tutti i clienti».")
    ambiti = [x for x in s.AMBITI if x in _lista(form, "ambiti")]
    if not ambiti:
        errori.append("Scegli almeno un tipo di bando.")
    au.ambiti = None if len(ambiti) == len(s.AMBITI) else ambiti
    punteggio = _intero(form, "punteggio_minimo")
    au.punteggio_minimo = min(max(punteggio if punteggio is not None else 70, 45), 100)
    azione = testo(form, "azione") or "bozza"
    au.azione = azione if azione in s.AZIONI else "bozza"
    canale = testo(form, "canale") or "preferito"
    au.canale = canale if canale in s.CANALI else "preferito"
    au.oggetto = (testo(form, "oggetto") or "")[:300]
    au.testo = (form.get("testo") or "").strip() if isinstance(form.get("testo"), str) else ""
    if not au.oggetto or not au.testo:
        errori.append("Scrivi oggetto e testo del messaggio.")
    elif au.evento:
        errori += s.errori_modello(au.oggetto, au.testo, au.evento)
    au.usa_ai = form.get("usa_ai") == "1"
    return errori


def _attiva(au: Automazione, accesa: bool) -> None:
    if accesa and not au.attiva:
        au.attiva_dal = datetime.now(UTC)  # niente arretrati: solo da adesso
    au.attiva = accesa


@router.get("/automazioni")
def elenco(request: Request, db: Db, _: Autenticato) -> Response:
    automazioni = list(db.scalars(select(Automazione).order_by(Automazione.id)))
    conteggi: dict[tuple[int, str], int] = {
        (au_id, stato): n
        for au_id, stato, n in db.execute(
            select(
                AggiornamentoCliente.automazione_id,
                AggiornamentoCliente.stato,
                func.count(),
            )
            .where(AggiornamentoCliente.automazione_id.is_not(None))
            .group_by(AggiornamentoCliente.automazione_id, AggiornamentoCliente.stato)
        )
    }
    return render(
        request,
        "automazioni/elenco.html",
        {
            "automazioni": automazioni,
            "conteggi": conteggi,
            "descrivi": _descrivi,
            "destinatari": s.DESTINATARI,
            "azioni": s.AZIONI,
            "msg": request.query_params.get("msg"),
            "esito": request.query_params.get("esito"),
        },
    )


@router.get("/automazioni/nuova")
def nuova(request: Request, db: Db, _: Titolare) -> Response:
    evento = request.query_params.get("evento") or "nuovo_bando"
    evento = evento if evento in s.EVENTI else "nuovo_bando"
    base = (
        s.TESTO_RIEPILOGO
        if evento == "riepilogo"
        else next(p for p in s.PREDEFINITE if p["evento"] == evento)
    )
    au = Automazione(
        nome="",
        evento=evento,
        giorni_scadenza=15 if evento == "scadenza" else None,
        frequenza="mensile" if evento == "riepilogo" else None,
        destinatari="tutti",
        punteggio_minimo=70,
        azione="bozza",
        canale="preferito",
        oggetto=base["oggetto"],
        testo=base["testo"],
        usa_ai=False,
        attiva=False,
    )
    return render(request, "automazioni/modulo.html", _contesto_form(db, au) | {"nuova": True})


@router.post("/automazioni/nuova", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Titolare, form: Form) -> Response:
    au = Automazione(attiva=False)
    errori = _leggi(au, form)
    if errori:
        ctx = _contesto_form(db, au) | {"nuova": True, "errori": errori}
        return render(request, "automazioni/modulo.html", ctx, 400)
    au.utente_id = utente.id
    _attiva(au, form.get("attiva") == "1")
    db.add(au)
    db.flush()
    audit.registra(db, utente, "automazione", au.id, "crea", {"nome": au.nome})
    db.commit()
    msg = "Automazione creata." + ("" if au.attiva else " È spenta: accendila quando è pronta.")
    return RedirectResponse(f"/automazioni/{au.id}?esito=ok&msg={quote(msg)}", status_code=303)


@router.get("/automazioni/{au_id}")
def scheda(request: Request, au_id: int, db: Db, utente: Autenticato) -> Response:
    au = _au(db, au_id)
    ctx = _contesto_form(db, au)
    # Anteprima con un cliente vero (dati di oggi, senza contare quelli già inviati)
    anteprima = None
    cliente_id = request.query_params.get("cliente")
    if cliente_id and cliente_id.isdigit():
        a = db.get(Azienda, int(cliente_id), options=[selectinload(Azienda.referenti)])
        if a is not None:
            m = s.messaggio(db, a, au, date.today(), anteprima=True)
            referente, _canale = agg.destinatario(
                a, None if au.canale == "preferito" else au.canale
            )
            if m is None:
                anteprima = {"cliente": a, "vuota": True}
            else:
                oggetto, corpo, _citate = m
                sost = {
                    "{nome_referente}": referente.nome if referente else "(referente)",
                    "{ragione_sociale}": a.ragione_sociale,
                    "{nome_consulente}": (s.responsabile(db, au) or utente).nome,
                }
                for k, v in sost.items():
                    oggetto, corpo = oggetto.replace(k, v), corpo.replace(k, v)
                anteprima = {
                    "cliente": a,
                    "oggetto": oggetto,
                    "testo": corpo,
                    "referente": referente,
                }
    registro = list(
        db.scalars(
            select(AggiornamentoCliente)
            .where(AggiornamentoCliente.automazione_id == au.id)
            .options(
                selectinload(AggiornamentoCliente.azienda),
                selectinload(AggiornamentoCliente.referente),
            )
            .order_by(AggiornamentoCliente.creato_il.desc())
            .limit(50)
        )
    )
    ctx |= {
        "anteprima": anteprima,
        "cliente_anteprima": int(cliente_id) if cliente_id and cliente_id.isdigit() else None,
        "registro": registro,
        "etichette_stato": ETICHETTE_STATO,
        "descrizione": _descrivi(au),
        "msg": request.query_params.get("msg"),
        "esito": request.query_params.get("esito"),
    }
    return render(request, "automazioni/modulo.html", ctx)


@router.post("/automazioni/{au_id}", dependencies=CSRF)
def salva(request: Request, au_id: int, db: Db, utente: Titolare, form: Form) -> Response:
    au = _au(db, au_id)
    with db.no_autoflush:  # con errori non si salva niente: la sessione si chiude senza commit
        errori = _leggi(au, form)
        if errori:
            ctx = _contesto_form(db, au) | {"errori": errori, "registro": []}
            return render(request, "automazioni/modulo.html", ctx, 400)
    au.utente_id = utente.id
    _attiva(au, form.get("attiva") == "1")
    audit.registra(db, utente, "automazione", au.id, "modifica", {"nome": au.nome})
    db.commit()
    return RedirectResponse(
        f"/automazioni/{au.id}?esito=ok&msg={quote('Automazione salvata.')}", status_code=303
    )


@router.post("/automazioni/{au_id}/accendi", dependencies=CSRF)
def accendi(au_id: int, db: Db, utente: Titolare, form: Form) -> Response:
    au = _au(db, au_id)
    _attiva(au, form.get("attiva") == "1")
    audit.registra(db, utente, "automazione", au.id, "modifica", {"attiva": au.attiva})
    db.commit()
    msg = f"«{au.nome}» {'accesa' if au.attiva else 'spenta'}."
    return RedirectResponse(f"/automazioni?esito=ok&msg={quote(msg)}", status_code=303)


@router.post("/automazioni/{au_id}/esegui", dependencies=CSRF)
def esegui_ora(au_id: int, db: Db, utente: Titolare) -> Response:
    """Esegue subito (come farebbe la mattina), anche se è spenta: serve a provarla."""
    au = _au(db, au_id)
    if au.attiva_dal is None:
        au.attiva_dal = datetime.now(UTC)
    risultato = s.esegui_una(db, au, base_url=get_settings().url_pubblico.rstrip("/"))
    audit.registra(
        db, utente, "automazione", au.id, "modifica", {"eseguita_a_mano": risultato.testo()}
    )
    db.commit()
    msg = f"Eseguita: {risultato.testo()}."
    return RedirectResponse(f"/automazioni/{au.id}?esito=ok&msg={quote(msg)}", status_code=303)


@router.post("/automazioni/{au_id}/elimina", dependencies=CSRF)
def elimina(au_id: int, db: Db, utente: Titolare) -> Response:
    au = _au(db, au_id)
    nome = au.nome
    audit.registra(db, utente, "automazione", au.id, "elimina", {"nome": nome})
    db.delete(au)  # i messaggi già creati restano in Aggiornamenti
    db.commit()
    msg = f"Automazione «{nome}» eliminata. I messaggi già preparati restano in Aggiornamenti."
    return RedirectResponse(f"/automazioni?esito=ok&msg={quote(msg)}", status_code=303)
