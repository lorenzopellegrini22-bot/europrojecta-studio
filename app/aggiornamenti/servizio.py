"""Aggiornamenti ai clienti (brief, sezione 11; preventivo, punto 03).

Scopo: far vedere al cliente che il consulente lavora per lui («questo periodo abbiamo
analizzato N nuovi bandi; questi possono interessarvi, per questi motivi; ecco a che punto è
la vostra pratica»).

- Le BOZZE nascono da sole con la frequenza scelta per ogni azienda (settimanale o mensile)
  oppure a richiesta. Partono dalle opportunità giudicate utili/interessanti o con punteggio
  alto, dalle pratiche aperte e dalle scadenze.
- Il testo è scritto dal server con segnaposto ({nome_referente}, {ragione_sociale},
  {nome_consulente}); se l'AI è attiva può riscriverlo in modo più naturale ricevendo SOLO dati
  dei bandi e dati anonimi: i segnaposto si sostituiscono sul server, i nomi non escono mai.
- Nessun invio senza approvazione esplicita; destinatari solo i referenti con il consenso per
  quel canale e non disiscritti; ogni email ha il link per non ricevere più aggiornamenti.
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.aggiornamenti.canali import CanaleEmail, CanaleWhatsApp, Esito
from app.models import (
    AggiornamentoCliente,
    Azienda,
    Bando,
    Opportunita,
    Pratica,
    Referente,
    Scadenza,
    Utente,
)
from app.sicurezza.cifratura import impronta

log = logging.getLogger(__name__)

SCOPO_DISISCRIZIONE = "disiscrizione"
GIORNI_FREQUENZA = {"settimanale": 7, "mensile": 30}
SEGNAPOSTO = ("{nome_referente}", "{ragione_sociale}", "{nome_consulente}")
PROMPT_AI = (
    "Riscrivi in italiano semplice e cordiale il messaggio di aggiornamento che un consulente "
    "di finanza agevolata manda a un'azienda cliente. Mantieni ESATTAMENTE i segnaposto tra "
    "parentesi graffe ({nome_referente}, {ragione_sociale}, {nome_consulente}) e tutti i fatti "
    "(titoli dei bandi, scadenze, stati delle pratiche). Non aggiungere informazioni, promesse "
    "o nomi. Massimo 1200 caratteri. Rispondi solo con il testo del messaggio."
)


# --- Destinatari e disiscrizione ----------------------------------------------------------


def token_disiscrizione(referente_id: int) -> str:
    return f"{referente_id}.{impronta(str(referente_id), SCOPO_DISISCRIZIONE)[:32]}"


def referente_da_token(db: Session, token: str) -> Referente | None:
    rid, _, firma = token.partition(".")
    if not rid.isdigit() or token != token_disiscrizione(int(rid)):
        return None
    return db.get(Referente, int(rid))


def destinatario(a: Azienda, canale: str | None = None) -> tuple[Referente | None, str | None]:
    """(referente, canale) a cui inviare: solo con consenso e non disiscritto. Preferenza:
    canale richiesto → canale preferito del referente → email."""
    candidati = sorted(a.referenti, key=lambda r: not r.principale)
    for r in candidati:
        if r.disiscritto_il:
            continue
        ammessi = [
            c
            for c, ok in (
                ("email", r.consenso_email and r.email),
                ("whatsapp", r.consenso_whatsapp and r.telefono),
            )
            if ok
        ]
        if not ammessi:
            continue
        if canale:
            if canale in ammessi:
                return r, canale
            continue
        if r.canale_preferito in ammessi:
            return r, r.canale_preferito
        return r, ammessi[0]
    return None, None


# --- Bozze ------------------------------------------------------------------------------


def _contenuto(db: Session, a: Azienda, dal: date, soglia: int) -> dict[str, Any]:
    # Bandi davvero nuovi nel periodo: si conta da dopo la prima raccolta (che importa tutto
    # l'archivio storico: contarla direbbe al cliente migliaia di «nuovi» bandi, falso)
    from app.models import FonteRun

    prima_raccolta = db.scalar(select(func.min(FonteRun.fine)).where(FonteRun.esito == "ok"))
    inizio = datetime.combine(dal, datetime.min.time(), UTC)
    if prima_raccolta is None:
        nuovi_bandi = 0
    else:
        nuovi_bandi = db.scalar(
            select(func.count())
            .select_from(Bando)
            .where(
                Bando.visto_la_prima_volta >= max(inizio, prima_raccolta),
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
            )
        )
    gia_citati: set[int] = set()
    for (citati,) in db.execute(
        select(AggiornamentoCliente.bandi_citati).where(
            AggiornamentoCliente.azienda_id == a.id,
            AggiornamentoCliente.stato.in_(("inviato", "approvato")),
        )
    ):
        gia_citati.update(citati or [])
    opportunita = [
        o
        for o in db.scalars(
            select(Opportunita)
            .where(
                Opportunita.azienda_id == a.id,
                Opportunita.bando_id.is_not(None),
                Opportunita.stato.in_(("nuova", "vista", "interessante", "in_pratica")),
            )
            .options(selectinload(Opportunita.bando))
            .order_by(Opportunita.punteggio.desc())
        )
        if o.bando_id not in gia_citati
        and (o.feedback == "utile" or o.stato == "interessante" or o.punteggio >= soglia)
        and o.feedback not in ("non_pertinente", "gia_noto")
    ][:3]
    pratiche = list(
        db.scalars(
            select(Pratica)
            .where(Pratica.azienda_id == a.id, Pratica.stato.not_in(("chiusa", "respinta")))
            .options(selectinload(Pratica.fasi), selectinload(Pratica.bando))
        )
    )
    scadenze = list(
        db.scalars(
            select(Scadenza)
            .where(
                Scadenza.azienda_id == a.id,
                Scadenza.completata.is_(False),
                Scadenza.data.between(date.today(), date.today() + timedelta(days=30)),
            )
            .order_by(Scadenza.data)
        )
    )
    return {
        "nuovi_bandi": nuovi_bandi or 0,
        "opportunita": opportunita,
        "pratiche": pratiche,
        "scadenze": scadenze,
    }


def testo_bozza(dati: dict[str, Any]) -> str:
    """Testo con segnaposto, scritto dal server (nessuna AI)."""
    righe = ["Buongiorno {nome_referente},", ""]
    n = dati["nuovi_bandi"]
    righe.append(
        f"in questo periodo abbiamo analizzato {n} nuovi bandi e incentivi pubblici "
        "per {ragione_sociale}."
        if n
        else "come sempre stiamo monitorando bandi e incentivi pubblici per {ragione_sociale}."
    )
    if dati["opportunita"]:
        righe += ["", "Questi possono interessarvi:"]
        for o in dati["opportunita"]:
            b = o.bando
            scad = f" (scadenza {b.data_chiusura:%d/%m/%Y})" if b.data_chiusura else ""
            righe.append(f"• {b.titolo}{scad}")
            motivo = o.motivazione_ai or "; ".join((o.motivi or {}).get("soddisfatti", [])[:2])
            if motivo:
                righe.append(f"  Perché: {motivo}")
    else:
        righe += [
            "",
            "Al momento non ci sono nuovi bandi adatti: vi avviseremo appena ne esce uno.",
        ]
    if dati["pratiche"]:
        righe += ["", "A che punto sono le vostre pratiche:"]
        for p in dati["pratiche"]:
            fase = p.fase_attiva
            stato = f"fase «{fase.nome}»" if fase else p.stato.replace("_", " ")
            righe.append(f"• {p.nome_bando}: {stato}")
    if dati["scadenze"]:
        righe += ["", "Prossime scadenze:"]
        righe += [f"• {s.data:%d/%m/%Y}: {s.titolo}" for s in dati["scadenze"]]
    righe += ["", "Per qualsiasi domanda sono a disposizione.", "{nome_consulente}"]
    return "\n".join(righe)


def _riscrivi_con_ai(db: Session, testo: str) -> str | None:
    """L'AI riceve solo il testo con segnaposto (dati dei bandi, nessun nome). Se perde un
    segnaposto o fallisce, si tiene il testo del server."""
    from app.extraction import servizio as ai
    from app.integrazioni import servizio as integrazioni

    s = integrazioni.stato(db, "openai")
    if not s.configurata or not s.attiva or ai.stato_budget(db)["superato"]:
        return None
    imp = ai.impostazioni(db)
    modello = imp.modello_scoring or integrazioni.leggi_valori(db, "openai").get("modello")
    if not modello:
        return None
    try:
        import openai

        client = openai.OpenAI(
            api_key=integrazioni.leggi_valori(db, "openai")["api_key"], timeout=60.0
        )
        r = client.responses.create(model=modello, instructions=PROMPT_AI, input=testo)
        uso = getattr(r, "usage", None)
        ai.registra_uso(
            db,
            "aggiornamento",
            modello,
            int(getattr(uso, "input_tokens", 0) or 0),
            int(getattr(uso, "output_tokens", 0) or 0),
            "ok",
        )
        nuovo = (r.output_text or "").strip()
    except Exception as e:  # l'AI è facoltativa: in caso di errore resta il testo del server
        log.warning("Riscrittura AI dell'aggiornamento non riuscita: %s", type(e).__name__)
        return None
    if not nuovo or any(s in testo and s not in nuovo for s in SEGNAPOSTO):
        return None
    return nuovo[:2000]


def crea_bozza(
    db: Session, a: Azienda, usa_ai: bool = False, oggi: date | None = None
) -> AggiornamentoCliente | None:
    """Nuova bozza per l'azienda (None se nessun referente ha dato il consenso)."""
    from app.extraction.servizio import impostazioni

    oggi = oggi or date.today()
    referente, canale = destinatario(a)
    if referente is None:
        return None
    giorni = GIORNI_FREQUENZA.get(a.frequenza_aggiornamenti, 30)
    dati = _contenuto(db, a, oggi - timedelta(days=giorni), impostazioni(db).soglia_notifica)
    testo = testo_bozza(dati)
    riscritto = _riscrivi_con_ai(db, testo) if usa_ai else None
    bozza = AggiornamentoCliente(
        azienda_id=a.id,
        referente_id=referente.id,
        canale=canale,
        oggetto="Aggiornamento su bandi e pratiche per {ragione_sociale}",
        testo=riscritto or testo,
        bandi_citati=[o.bando_id for o in dati["opportunita"]] or None,
        pratiche_citate=[p.id for p in dati["pratiche"]] or None,
        stato="bozza",
        generato_con_ai=riscritto is not None,
    )
    db.add(bozza)
    db.flush()
    return bozza


def bozze_periodiche(db: Session, oggi: date | None = None) -> str:
    """Per le aziende attive con frequenza settimanale o mensile: una bozza quando dall'ultima
    (non scartata) è passato il periodo. Non invia nulla."""
    oggi = oggi or date.today()
    create = 0
    for a in db.scalars(
        select(Azienda)
        .where(
            Azienda.frequenza_aggiornamenti.in_(tuple(GIORNI_FREQUENZA)),
            Azienda.stato_cliente == "attivo",
        )
        .options(selectinload(Azienda.referenti))
    ):
        ultima = db.scalar(
            select(func.max(AggiornamentoCliente.creato_il)).where(
                AggiornamentoCliente.azienda_id == a.id,
                AggiornamentoCliente.stato != "scartato",
            )
        )
        limite = datetime.combine(oggi, datetime.min.time(), UTC) - timedelta(
            days=GIORNI_FREQUENZA[a.frequenza_aggiornamenti]
        )
        if ultima is not None and ultima > limite:
            continue
        if crea_bozza(db, a, oggi=oggi) is not None:
            create += 1
    db.commit()
    return f"Bozze di aggiornamento create: {create}."


# --- Invio -------------------------------------------------------------------------------


def testo_finale(ag: AggiornamentoCliente, consulente: Utente, base_url: str) -> tuple[str, str]:
    """(oggetto, testo) con i segnaposto sostituiti e, per l'email, il link di disiscrizione."""
    r = ag.referente
    sostituzioni = {
        "{nome_referente}": r.nome if r else "",
        "{ragione_sociale}": ag.azienda.ragione_sociale,
        "{nome_consulente}": consulente.nome,
    }
    oggetto, testo = ag.oggetto, ag.testo
    for k, v in sostituzioni.items():
        # «per Rossi S.r.l.» a fine frase: niente doppio punto
        if v.endswith("."):
            oggetto, testo = oggetto.replace(k + ".", v), testo.replace(k + ".", v)
        oggetto, testo = oggetto.replace(k, v), testo.replace(k, v)
    if ag.canale == "email" and r is not None:
        testo += (
            "\n\n--\nRicevi questi aggiornamenti perché hai dato il consenso al tuo consulente. "
            f"Per non riceverli più: {base_url}/disiscrizione/{token_disiscrizione(r.id)}"
        )
    return oggetto, testo


def invia(
    db: Session, ag: AggiornamentoCliente, consulente: Utente, base_url: str, client: Any = None
) -> Esito:
    """Invia un aggiornamento APPROVATO. Ricontrolla consenso e disiscrizione al momento
    dell'invio."""
    if ag.stato != "approvato":
        return Esito(ok=False, errore="L'aggiornamento non è approvato.")
    r = ag.referente
    consenso = (
        r is not None
        and not r.disiscritto_il
        and (
            (ag.canale == "email" and r.consenso_email and r.email)
            or (ag.canale == "whatsapp" and r.consenso_whatsapp and r.telefono)
        )
    )
    if not consenso:
        esito = Esito(ok=False, errore="Il referente non ha il consenso per questo canale.")
    else:
        canale = (
            CanaleEmail(db, consulente.id) if ag.canale == "email" else CanaleWhatsApp(db, client)
        )
        if not canale.disponibile():
            esito = Esito(
                ok=False,
                errore=(
                    f"Il canale {'email' if ag.canale == 'email' else 'WhatsApp'} non è "
                    "collegato o è disattivato: collegalo in Impostazioni → Integrazioni."
                ),
            )
        else:
            oggetto, testo = testo_finale(ag, consulente, base_url)
            dest = r.email if ag.canale == "email" else r.telefono
            esito = canale.invia(dest, oggetto, testo, r.nome)
    if esito.ok:
        ag.stato, ag.inviato_il, ag.id_messaggio, ag.errore = (
            "inviato",
            datetime.now(UTC),
            esito.id_messaggio,
            None,
        )
    else:
        ag.stato, ag.errore = "errore", esito.errore
    return esito


def riepilogo_json(ag: AggiornamentoCliente) -> str:
    return json.dumps(
        {"bandi": ag.bandi_citati or [], "pratiche": ag.pratiche_citate or []}, ensure_ascii=False
    )
