"""Estrazione dei requisiti dei bandi con l'AI (OpenAI, account del consulente).

- Lavora solo sui bandi aperti o in arrivo, e solo se è cambiato l'input: hash di testo,
  versione del prompt, versione dello schema e modello.
- Input: solo il testo pubblico del bando (campi descrittivi della fonte).
- Output strutturato (JSON Schema strict), poi verificato (citazioni, vocabolari) e risolto
  nei requisiti con la regola correzione manuale > fonte > AI.
- Ogni chiamata è registrata in uso_llm con il costo stimato dai prezzi delle impostazioni.
- Budget mensile: all'80% un avviso email, al 100% le chiamate si fermano fino al mese dopo.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, selectinload

from app.extraction.applica import ricalcola
from app.extraction.schema import VERSIONE_SCHEMA, schema_json
from app.extraction.verifica import verifica
from app.integrazioni import servizio as integrazioni
from app.models import Bando
from app.models.ai import EstrazioneBando, ImpostazioniAI, UsoLLM

log = logging.getLogger(__name__)

VERSIONE_PROMPT = "v1"
PROMPT = (Path(__file__).parent / "prompts" / f"estrazione_{VERSIONE_PROMPT}.md").read_text(
    encoding="utf-8"
)
MAX_CARATTERI = 40_000  # testo della fonte + brani dei PDF
# Campi testuali della fonte nazionale utili all'estrazione (oltre a titolo e descrizione)
CAMPI_RAW = {
    "Costi_Ammessi": "Costi ammessi",
    "Codici_ATECO": "Codici ATECO",
    "Settore_Attivita": "Settori",
    "Dimensione_beneficiari": "Dimensione dei beneficiari",
    "Tipologia_Soggetto": "Soggetti beneficiari",
    "Forma_agevolazione": "Forma dell'agevolazione",
    "Note_di_apertura_chiusura": "Note su apertura e chiusura",
    "Base_normativa_primaria": "Base normativa",
    "Provvedimento_attuativo": "Provvedimento attuativo",
    "Cumulabilita": "Cumulabilità",
    "Spesa_Ammessa_Min": "Spesa ammessa minima (euro)",
    "Spesa_Ammessa_Max": "Spesa ammessa massima (euro)",
    "Agevolazione_Concedibile_Min": "Agevolazione minima (euro)",
    "Agevolazione_Concedibile_Max": "Agevolazione massima (euro)",
    "Stanziamento_incentivo": "Dotazione (euro)",
    "Data_apertura": "Data di apertura",
    "Data_chiusura": "Data di chiusura",
    "testo": None,  # fonti regionali: testo della pagina (già in descrizione)
}


class ErroreEstrazione(Exception):
    """Errore che ferma il run (chiave non valida, credito esaurito, budget)."""


@dataclass
class Configurazione:
    modello: str
    api_key: str
    impostazioni: ImpostazioniAI


def impostazioni(db: Session) -> ImpostazioniAI:
    imp = db.get(ImpostazioniAI, 1)
    if imp is None:
        imp = ImpostazioniAI(id=1, prezzi={}, estrazioni_per_notte=50)
        db.add(imp)
        db.commit()
    return imp


def configurazione(db: Session) -> tuple[Configurazione | None, str]:
    s = integrazioni.stato(db, "openai")
    if not s.configurata:
        return None, "OpenAI non è collegato (Impostazioni → Integrazioni)."
    if not s.attiva:
        return None, "L'integrazione OpenAI è disattivata."
    valori = integrazioni.leggi_valori(db, "openai")
    imp = impostazioni(db)
    modello = imp.modello_estrazione or valori.get("modello")
    if not modello:
        return None, "Manca il modello per l'estrazione (Impostazioni → Intelligenza artificiale)."
    return Configurazione(modello=modello, api_key=valori["api_key"], impostazioni=imp), ""


def testo_bando(b: Bando, db: Session | None = None) -> str:
    parti = [f"Titolo: {b.titolo}"]
    if b.ente:
        parti.append(f"Ente: {b.ente}")
    if b.descrizione:
        parti.append(f"Descrizione:\n{b.descrizione}")
    raw = b.raw or {}
    for chiave, etichetta in CAMPI_RAW.items():
        v = raw.get(chiave)
        if etichetta is None or v in (None, "", [], 0, 0.0):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        parti.append(f"{etichetta}: {v}")
    if b.note_apertura_chiusura:
        parti.append(f"Note: {b.note_apertura_chiusura}")
    base = "\n".join(parti)[:MAX_CARATTERI]
    if db is not None and b.id is not None:
        from app.documenti.servizio import testo_per_ai

        base += testo_per_ai(db, b.id, MAX_CARATTERI - len(base))
    return base


def hash_input(testo: str, modello: str) -> str:
    chiave = f"{VERSIONE_PROMPT}|{VERSIONE_SCHEMA}|{modello}|{testo}"
    return hashlib.sha256(chiave.encode("utf-8")).hexdigest()


# --- Costi e budget ----------------------------------------------------------------------


def costo(imp: ImpostazioniAI, modello: str, ingresso: int, uscita: int) -> Decimal | None:
    prezzi = (imp.prezzi or {}).get(modello)
    if not prezzi:
        return None
    try:
        p_in = Decimal(str(prezzi["ingresso"]))
        p_out = Decimal(str(prezzi["uscita"]))
    except (KeyError, ArithmeticError, ValueError):
        return None
    return (p_in * ingresso + p_out * uscita) / Decimal(1_000_000)


def spesa_mese(db: Session, oggi: date | None = None) -> Decimal:
    oggi = oggi or date.today()
    inizio = datetime(oggi.year, oggi.month, 1, tzinfo=UTC)
    totale = db.scalar(
        select(func.coalesce(func.sum(UsoLLM.costo_stimato), 0)).where(UsoLLM.creato_il >= inizio)
    )
    return Decimal(totale or 0)


def stato_budget(db: Session, oggi: date | None = None) -> dict[str, Any]:
    imp = impostazioni(db)
    spesa = spesa_mese(db, oggi)
    budget = imp.budget_mensile_eur
    percentuale = int(spesa / budget * 100) if budget else None
    return {
        "spesa": spesa,
        "budget": budget,
        "percentuale": percentuale,
        "superato": bool(budget) and spesa >= budget,
        "sospesa": imp.sospesa_fino_al is not None and imp.sospesa_fino_al > (oggi or date.today()),
        "motivo": imp.motivo_sospensione,
    }


def registra_uso(
    db: Session,
    operazione: str,
    modello: str,
    ingresso: int,
    uscita: int,
    esito: str,
    errore: str | None = None,
) -> Decimal | None:
    imp = impostazioni(db)
    c = costo(imp, modello, ingresso, uscita)
    db.add(
        UsoLLM(
            operazione=operazione,
            modello=modello,
            token_ingresso=ingresso,
            token_uscita=uscita,
            costo_stimato=c,
            esito=esito,
            errore=errore,
        )
    )
    return c


# --- Estrazione --------------------------------------------------------------------------


def chiama_modello(client: Any, modello: str, testo: str) -> tuple[dict[str, Any], int, int]:
    """Una chiamata con output strutturato; restituisce (output, token in, token out)."""
    risposta = client.responses.create(
        model=modello,
        instructions=PROMPT,
        input=testo,
        text={
            "format": {
                "type": "json_schema",
                "name": "requisiti_bando",
                "schema": schema_json(),
                "strict": True,
            }
        },
    )
    uso = getattr(risposta, "usage", None)
    ingresso = int(getattr(uso, "input_tokens", 0) or 0)
    uscita = int(getattr(uso, "output_tokens", 0) or 0)
    return json.loads(risposta.output_text), ingresso, uscita


def estrai_bando(db: Session, bando: Bando, conf: Configurazione, client: Any) -> EstrazioneBando:
    """Estrae, verifica, salva e ricalcola i requisiti di un bando."""
    import openai

    testo = testo_bando(bando, db)
    estrazione = EstrazioneBando(
        bando_id=bando.id,
        modello=conf.modello,
        versione_prompt=VERSIONE_PROMPT,
        versione_schema=VERSIONE_SCHEMA,
        hash_input=hash_input(testo, conf.modello),
        stato_revisione="da_revisionare",
    )
    try:
        grezzo, ingresso, uscita = chiama_modello(client, conf.modello, testo)
    except openai.AuthenticationError as e:
        raise ErroreEstrazione("La chiave OpenAI non è valida o è stata revocata.") from e
    except openai.PermissionDeniedError as e:
        raise ErroreEstrazione("La chiave OpenAI non ha accesso a questo modello.") from e
    except openai.NotFoundError as e:
        raise ErroreEstrazione(
            f"Il modello «{conf.modello}» non esiste o non è disponibile."
        ) from e
    except openai.RateLimitError as e:
        codice = getattr(e, "code", None) or ""
        if "insufficient_quota" in str(codice) or "insufficient_quota" in str(e):
            raise ErroreEstrazione("Credito OpenAI esaurito: ricarica l'account.") from e
        raise ErroreEstrazione("Limite di richieste OpenAI raggiunto: si riprova domani.") from e
    except (openai.APIError, json.JSONDecodeError) as e:
        estrazione.errore = f"{type(e).__name__}: {e}"[:2000]
        registra_uso(db, "estrazione", conf.modello, 0, 0, "errore", estrazione.errore)
        db.add(estrazione)
        db.commit()
        return estrazione
    estrazione.output = verifica(grezzo, testo)
    estrazione.token_ingresso = ingresso
    estrazione.token_uscita = uscita
    estrazione.costo_stimato = registra_uso(db, "estrazione", conf.modello, ingresso, uscita, "ok")
    db.add(estrazione)
    db.flush()
    ricalcola(db, bando)
    db.commit()
    return estrazione


def da_estrarre(db: Session, modello: str, limite: int) -> list[Bando]:
    """Bandi aperti o in arrivo il cui input è cambiato dall'ultima estrazione riuscita."""
    ultimi = {
        bid: h
        for bid, h in db.execute(
            select(EstrazioneBando.bando_id, EstrazioneBando.hash_input)
            .where(EstrazioneBando.output.is_not(None))
            .order_by(EstrazioneBando.bando_id, EstrazioneBando.id.desc())
            .ext(distinct_on(EstrazioneBando.bando_id))
        ).all()
    }
    candidati = db.scalars(
        select(Bando)
        .where(Bando.stato.in_(("aperto", "in_arrivo")), Bando.bando_canonico_id.is_(None))
        .options(selectinload(Bando.requisiti))
        .order_by(Bando.data_chiusura.asc().nulls_last(), Bando.id)
    )
    scelti = []
    for b in candidati:
        if ultimi.get(b.id) != hash_input(testo_bando(b, db), modello):
            scelti.append(b)
            if len(scelti) >= limite:
                break
    return scelti


def esegui(db: Session, client: Any = None, oggi: date | None = None) -> str:
    """Il run notturno. Si ferma (senza errori) se l'AI non è configurata o il budget è finito."""
    oggi = oggi or date.today()
    conf, motivo = configurazione(db)
    if conf is None:
        return f"Estrazione non eseguita: {motivo}"
    imp = conf.impostazioni
    if imp.sospesa_fino_al and imp.sospesa_fino_al > oggi:
        return (
            f"Estrazione sospesa fino al {imp.sospesa_fino_al:%d/%m/%Y}: {imp.motivo_sospensione}"
        )
    if imp.budget_mensile_eur and conf.modello not in (imp.prezzi or {}):
        return (
            "Estrazione non eseguita: con un budget impostato servono i prezzi del modello "
            f"«{conf.modello}» (Impostazioni → Intelligenza artificiale)."
        )
    if client is None:
        import openai

        client = openai.OpenAI(api_key=conf.api_key, timeout=90.0, max_retries=3)
    bandi = da_estrarre(db, conf.modello, imp.estrazioni_per_notte)
    fatte = errori = 0
    for b in bandi:
        budget = stato_budget(db, oggi)
        if budget["superato"]:
            _sospendi(db, imp, oggi, "budget mensile esaurito")
            break
        try:
            e = estrai_bando(db, b, conf, client)
        except ErroreEstrazione as err:
            db.rollback()
            imp = impostazioni(db)
            imp.motivo_sospensione = str(err)
            db.commit()
            raise
        fatte += e.output is not None
        errori += e.output is None
    _avviso_budget(db, oggi)
    if imp.motivo_sospensione and not imp.sospesa_fino_al:
        imp.motivo_sospensione = None  # un run riuscito cancella il vecchio errore
        db.commit()
    return f"Estrazione: {fatte} bandi strutturati, {errori} non riusciti, {len(bandi)} in coda."


def _sospendi(db: Session, imp: ImpostazioniAI, oggi: date, motivo: str) -> None:
    primo_mese_dopo = date(oggi.year + (oggi.month == 12), oggi.month % 12 + 1, 1)
    imp.sospesa_fino_al = primo_mese_dopo
    imp.motivo_sospensione = motivo
    db.commit()


def _avviso_budget(db: Session, oggi: date) -> None:
    """Email all'80% del budget, una volta al mese, agli utenti titolari."""
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.models import Utente

    b = stato_budget(db, oggi)
    imp = impostazioni(db)
    mese = f"{oggi:%Y-%m}"
    if not b["budget"] or b["percentuale"] is None or b["percentuale"] < 80:
        return
    if imp.avviso_80_inviato_mese == mese or not email_disponibile(db):
        return
    for u in db.scalars(select(Utente).where(Utente.attivo, Utente.ruolo == "titolare")):
        invia_registrando(
            db,
            tipo="promemoria",
            destinatario=u.email,
            oggetto=f"Budget AI all'{b['percentuale']}%",
            testo=(
                f"La spesa stimata per l'intelligenza artificiale questo mese è "
                f"{b['spesa']:.2f} € su un budget di {b['budget']:.2f} €.\n"
                "Al 100% le estrazioni si fermano fino al mese successivo."
            ),
            utente=u,
        )
    imp.avviso_80_inviato_mese = mese
    db.commit()
