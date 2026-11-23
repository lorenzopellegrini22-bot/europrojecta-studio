"""Fase 4: punteggio AI delle opportunità.

1. Embeddings (se è impostato il modello): profilo anonimo del cliente e testo di ogni
   bando/appalto; ricalcolati solo se cambia l'hash del testo o il modello.
2. Preselezione: per ogni cliente le opportunità ancora da valutare, ordinate per similarità
   (o per punteggio delle regole se non ci sono embeddings), sopra la soglia, al massimo K.
3. Valutazione AI con griglia esplicita e output strutturato: punteggio 0-100, motivazione,
   requisiti soddisfatti / da verificare / mancanti. All'AI arrivano SOLO dati anonimi:
   mai ragione sociale, partita IVA, codice fiscale, referenti, contatti, note.
4. Niente ricalcoli: si rivaluta solo se cambia l'hash dell'input.
5. Limiti: numero massimo di valutazioni per esecuzione e budget mensile (come l'estrazione).
"""

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.extraction import servizio as ai
from app.integrazioni import servizio as integrazioni
from app.models import Azienda
from app.models.ai import Embedding
from app.models.opportunita import Opportunita
from app.utils.cpv import etichetta

log = logging.getLogger(__name__)

VERSIONE_PROMPT = "v1"
PROMPT = (Path(__file__).parent / "prompts" / f"valutazione_{VERSIONE_PROMPT}.md").read_text(
    encoding="utf-8"
)
SCHEMA = {
    "type": "object",
    "properties": {
        "punteggio": {"type": "integer"},
        "motivazione": {"type": "string"},
        "soddisfatti": {"type": "array", "items": {"type": "string"}},
        "da_verificare": {"type": "array", "items": {"type": "string"}},
        "mancanti": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["punteggio", "motivazione", "soddisfatti", "da_verificare", "mancanti"],
    "additionalProperties": False,
}


def _sha(testo: str) -> str:
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


# --- Testi -------------------------------------------------------------------------------


def _fascia_abitanti(n: int | None) -> str | None:
    """Fascia, non il numero esatto: con provincia e abitanti si riconoscerebbe il comune."""
    if n is None:
        return None
    for limite in (1000, 3000, 5000, 15000, 30000, 60000, 100000):
        if n <= limite:
            return f"fino a {limite:,} abitanti".replace(",", ".")
    return "oltre 100.000 abitanti"


def _profilo_ente(a: Azienda, progetti: list[Any] | None) -> str:
    righe = ["Ente pubblico cliente (dati anonimi): un ente locale, non un'impresa."]
    if a.forma_giuridica:
        righe.append(f"- tipo di ente: {a.forma_giuridica}")
    if fascia := _fascia_abitanti(getattr(a, "abitanti", None)):
        righe.append(f"- popolazione: {fascia}")
    sede = a.sede_legale
    if sede and (sede.regione or sede.provincia):
        righe.append(
            f"- territorio: regione {sede.regione or 'non indicata'}"
            + (f", provincia {sede.provincia}" if sede.provincia else "")
        )
    righe += _righe_progetti(progetti)
    return "\n".join(righe)


def _righe_progetti(progetti: list[Any] | None) -> list[str]:
    righe = []
    for i, pr in enumerate([p for p in progetti or [] if p.attivo][:3], start=1):
        parti = []
        if pr.descrizione:
            parti.append(pr.descrizione[:600])
        if pr.categorie:
            parti.append("spese: " + ", ".join(pr.categorie))
        if pr.importo_stimato:
            parti.append(f"importo circa {int(pr.importo_stimato):,} euro".replace(",", "."))
        if parti:
            righe.append(f"- progetto d'investimento {i}: " + "; ".join(parti))
    return righe


def profilo_anonimo(a: Azienda, progetti: list[Any] | None = None) -> str:
    """Solo dati non identificativi (docs/dati-in-uscita.md). Dei progetti d'investimento
    si mandano solo descrizione, categorie di spesa e importo (mai il titolo, che potrebbe
    contenere nomi)."""
    from app.utils.impresa import anzianita_mesi, dimensione_effettiva

    if getattr(a, "tipo_soggetto", None) == "ente_pubblico":
        return _profilo_ente(a, progetti)
    righe = ["Impresa cliente (dati anonimi):"]
    if a.forma_giuridica:
        righe.append(f"- forma giuridica: {a.forma_giuridica}")
    dimensione, provenienza = dimensione_effettiva(a)
    if dimensione:
        stima = " (stima)" if provenienza == "calcolata" else ""
        righe.append(f"- dimensione: {dimensione}{stima}")
    mesi = anzianita_mesi(getattr(a, "data_costituzione", None))
    if mesi is not None:
        righe.append(f"- costituita da {mesi // 12} anni e {mesi % 12} mesi")
    sede = a.sede_legale
    if sede and (sede.regione or sede.provincia):
        righe.append(
            f"- sede: regione {sede.regione or 'non indicata'}"
            + (f", provincia {sede.provincia}" if sede.provincia else "")
        )
    if a.ateco_primario:
        righe.append(
            f"- ATECO prevalente: {a.ateco_primario.codice} "
            f"{a.ateco_primario.descrizione or ''}".rstrip()
        )
    if a.dipendenti is not None:
        righe.append(f"- dipendenti: {a.dipendenti}")
    if a.fatturato is not None:
        righe.append(f"- fatturato annuo: circa {int(a.fatturato):,} euro".replace(",", "."))
    if a.soa_possedute:
        righe.append(
            "- attestazione SOA: "
            + ", ".join(
                f"{s['codice']} {s.get('classifica') or ''}".strip() for s in a.soa_possedute
            )
        )
    operative = sorted(
        {f"{s.regione} ({s.provincia})" if s.provincia else s.regione
         for s in getattr(a, "sedi", None) or [] if s.tipo == "operativa" and s.regione}
    )  # fmt: skip
    if operative:
        righe.append(f"- sedi operative in: {', '.join(operative)}")
    if a.regioni_interesse:
        righe.append(f"- partecipa agli appalti anche in: {', '.join(a.regioni_interesse)}")
    if a.cpv_interesse:
        righe.append(
            "- servizi e forniture di interesse: "
            + ", ".join(etichetta(c) for c in a.cpv_interesse)
        )
    righe += _righe_progetti(progetti)
    return "\n".join(righe)


def testo_opportunita(o: Opportunita, con_motivi: bool = True) -> str:
    """Scheda del bando/gara. Senza motivi (che dipendono dal cliente) per gli embeddings."""
    if o.bando is not None:
        b, r = o.bando, o.bando.requisiti
        righe = [f"BANDO: {b.titolo}", f"Ente: {b.ente or 'non indicato'}"]
        if b.data_chiusura:
            righe.append(f"Scadenza: {b.data_chiusura:%d/%m/%Y}")
        if r is not None:
            if r.sintesi:
                righe.append(f"Sintesi: {r.sintesi}")
            campi = {
                "Territorio": r.territorio,
                "Regioni": r.regioni,
                "Comuni": r.comuni,
                "Dimensioni ammesse": r.dimensioni,
                "ATECO ammessi": r.ateco_ammessi,
                "ATECO esclusi": r.ateco_esclusi,
                "Beneficiari": r.tipologie_soggetto,
                "Spese ammissibili": r.categorie_spesa,
                "Finalità": r.finalita,
                "Forma di agevolazione": r.forma_agevolazione,
                "Percentuale": r.percentuale,
                "Agevolazione massima": r.importo_max,
                "Regime": r.regime,
                "Modalità": r.modalita,
                "Anzianità massima (mesi)": r.anzianita_max_mesi,
            }
            for etichetta, v in campi.items():
                if v not in (None, [], ""):
                    righe.append(f"{etichetta}: {', '.join(v) if isinstance(v, list) else v}")
        elif b.descrizione:
            righe.append(f"Descrizione: {b.descrizione[:3000]}")
    else:
        a = o.appalto
        righe = [f"GARA D'APPALTO: {a.titolo}", f"Stazione appaltante: {a.stazione_appaltante}"]
        if a.scadenza:
            righe.append(f"Scadenza offerte: {a.scadenza:%d/%m/%Y}")
        if a.natura:
            righe.append(f"Tipo: {', '.join(a.natura)}")
        if a.categorie_soa:
            righe.append(
                "Categorie SOA: "
                + ", ".join(
                    f"{c['codice']} ({c.get('tipo') or 'tipo non indicato'})"
                    for c in a.categorie_soa
                )
            )
        if a.cpv:
            righe.append(f"CPV: {', '.join(a.cpv[:5])}")
        if a.importo_stimato:
            righe.append(f"Importo: {a.importo_stimato} euro")
        if a.regioni:
            righe.append(f"Luogo: {', '.join(a.regioni)}")
        if a.tipo_procedura:
            righe.append(f"Procedura: {a.tipo_procedura}")
        if a.descrizione:
            righe.append(f"Descrizione: {a.descrizione[:3000]}")
    motivi = (o.motivi or {}) if con_motivi else {}
    if motivi.get("soddisfatti") or motivi.get("da_verificare"):
        righe.append("Controlli automatici già fatti:")
        righe += [f"- ok: {m}" for m in motivi.get("soddisfatti") or []]
        righe += [f"- da verificare: {m}" for m in motivi.get("da_verificare") or []]
    return "\n".join(righe)


# --- Embeddings --------------------------------------------------------------------------


def vettori(
    db: Session,
    client: Any,
    modello: str,
    elementi: list[tuple[str, int, str]],
) -> dict[tuple[str, int], np.ndarray]:
    """(entita, id, testo) → vettore, con cache su database per hash del testo."""
    esistenti = {
        (e.entita, e.entita_id): e
        for e in db.scalars(select(Embedding).where(Embedding.modello == modello))
        if (e.entita, e.entita_id) in {(x[0], x[1]) for x in elementi}
    }
    risultato: dict[tuple[str, int], np.ndarray] = {}
    da_calcolare = []
    for entita, eid, testo in elementi:
        e = esistenti.get((entita, eid))
        if e is not None and e.hash_testo == _sha(testo):
            risultato[(entita, eid)] = np.asarray(e.vettore, dtype=float)
        else:
            da_calcolare.append((entita, eid, testo))
    for i in range(0, len(da_calcolare), 100):
        lotto = da_calcolare[i : i + 100]
        risposta = client.embeddings.create(model=modello, input=[t[2][:8000] for t in lotto])
        token = int(getattr(getattr(risposta, "usage", None), "prompt_tokens", 0) or 0)
        ai.registra_uso(db, "embedding", modello, token, 0, "ok")
        for (entita, eid, testo), dato in zip(lotto, risposta.data, strict=True):
            v = np.asarray(dato.embedding, dtype=float)
            risultato[(entita, eid)] = v
            e = esistenti.get((entita, eid))
            if e is None:
                db.add(
                    Embedding(
                        entita=entita,
                        entita_id=eid,
                        modello=modello,
                        hash_testo=_sha(testo),
                        vettore=v.tolist(),
                    )
                )
            else:
                e.hash_testo = _sha(testo)
                e.vettore = v.tolist()
    db.commit()
    return risultato


def coseno(a: np.ndarray, b: np.ndarray) -> float:
    n = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / n) if n else 0.0


# --- Valutazione -------------------------------------------------------------------------


def _modello_scoring(db: Session) -> str | None:
    imp = ai.impostazioni(db)
    return (
        imp.modello_scoring
        or imp.modello_estrazione
        or integrazioni.leggi_valori(db, "openai").get("modello")
    )


def hash_input(profilo: str, testo: str, modello: str) -> str:
    return _sha(f"{VERSIONE_PROMPT}|{modello}|{profilo}|{testo}")


def valuta_una(
    db: Session, client: Any, modello: str, o: Opportunita, profilo: str, testo: str
) -> bool:
    risposta = client.responses.create(
        model=modello,
        instructions=PROMPT,
        input=f"{profilo}\n\n{testo}",
        text={
            "format": {
                "type": "json_schema",
                "name": "valutazione_opportunita",
                "schema": SCHEMA,
                "strict": True,
            }
        },
    )
    uso = getattr(risposta, "usage", None)
    ai.registra_uso(
        db,
        "scoring",
        modello,
        int(getattr(uso, "input_tokens", 0) or 0),
        int(getattr(uso, "output_tokens", 0) or 0),
        "ok",
    )
    dati = json.loads(risposta.output_text)
    punteggio = int(dati.get("punteggio", 0))
    o.punteggio_ai = max(0, min(100, punteggio))
    o.punteggio = o.punteggio_ai
    o.motivazione_ai = str(dati.get("motivazione") or "")[:1500]
    o.requisiti_ai = {
        k: [str(x)[:200] for x in (dati.get(k) or [])][:5]
        for k in ("soddisfatti", "da_verificare", "mancanti")
    }
    o.modello_ai = modello[:100]
    o.hash_input_ai = hash_input(profilo, testo, modello)
    o.valutata_il = datetime.now(UTC)
    return True


def esegui(db: Session, client: Any = None, oggi: date | None = None) -> str:
    oggi = oggi or date.today()
    s = integrazioni.stato(db, "openai")
    if not s.configurata or not s.attiva:
        return "Valutazione AI non eseguita: OpenAI non collegato o disattivato."
    modello = _modello_scoring(db)
    if not modello:
        return "Valutazione AI non eseguita: manca il modello (Impostazioni → AI)."
    imp = ai.impostazioni(db)
    budget = ai.stato_budget(db, oggi)
    if budget["superato"] or budget["sospesa"]:
        return "Valutazione AI non eseguita: budget del mese esaurito."
    if imp.budget_mensile_eur and modello not in (imp.prezzi or {}):
        return f"Valutazione AI non eseguita: mancano i prezzi del modello «{modello}»."
    if client is None:
        import openai

        client = openai.OpenAI(
            api_key=integrazioni.leggi_valori(db, "openai")["api_key"], timeout=60.0, max_retries=3
        )
    clienti = list(
        db.scalars(
            select(Azienda)
            .where(Azienda.avvisi_attivi, Azienda.stato_cliente != "ex")
            .options(selectinload(Azienda.sedi), selectinload(Azienda.ateco))
        )
    )
    fatte = saltate = 0
    for a in clienti:
        opportunita = list(
            db.scalars(
                select(Opportunita)
                .where(
                    Opportunita.azienda_id == a.id,
                    Opportunita.stato.in_(("nuova", "vista", "interessante")),
                    Opportunita.feedback.is_(None),
                )
                .options(selectinload(Opportunita.bando), selectinload(Opportunita.appalto))
            )
        )
        if not opportunita:
            continue
        from app.models import Progetto

        profilo = profilo_anonimo(
            a, list(db.scalars(select(Progetto).where(Progetto.azienda_id == a.id)))
        )
        testi = {o.id: testo_opportunita(o) for o in opportunita}
        # Similarità (se c'è il modello di embeddings), altrimenti ordine per regole
        if imp.modello_embeddings:
            elementi = [("azienda", a.id, profilo)] + [
                (
                    "bando" if o.bando_id else "appalto",
                    o.bando_id or o.appalto_id,
                    testo_opportunita(o, con_motivi=False),
                )
                for o in opportunita
            ]
            vett = vettori(db, client, imp.modello_embeddings, elementi)
            vp = vett[("azienda", a.id)]
            for o in opportunita:
                chiave = ("bando" if o.bando_id else "appalto", o.bando_id or o.appalto_id)
                o.similarita = round(coseno(vp, vett[chiave]), 4)
            candidati = [o for o in opportunita if (o.similarita or 0) >= imp.soglia_similarita]
            candidati.sort(key=lambda o: -(o.similarita or 0))
        else:
            candidati = sorted(opportunita, key=lambda o: -(o.punteggio_regole or o.punteggio))
        for o in candidati[: imp.valutazioni_per_cliente]:
            if fatte >= imp.valutazioni_per_notte:
                break
            if o.hash_input_ai == hash_input(profilo, testi[o.id], modello):
                saltate += 1
                continue
            if ai.stato_budget(db, oggi)["superato"]:
                db.commit()
                return f"Valutazione AI: {fatte} opportunità valutate, poi budget esaurito."
            valuta_una(db, client, modello, o, profilo, testi[o.id])
            fatte += 1
            db.commit()
    db.commit()
    return f"Valutazione AI: {fatte} opportunità valutate, {saltate} già valutate e invariate."


# --- Precisione ------------------------------------------------------------------------

FASCE = (
    (80, 100, "80-100 adatta"),
    (60, 79, "60-79 probabile"),
    (40, 59, "40-59 dubbia"),
    (0, 39, "0-39 non adatta"),
)


def precisione(db: Session) -> list[dict[str, Any]]:
    """Per fascia di punteggio AI: quanti feedback e quanta parte era pertinente
    («utile» o «già noto» = pertinente; «non pertinente» = errore). In fondo le opportunità
    con solo il punteggio delle regole, per confronto."""
    righe = db.execute(
        select(Opportunita.punteggio_ai, Opportunita.punteggio, Opportunita.feedback).where(
            Opportunita.feedback.is_not(None)
        )
    ).all()
    fasce = [
        {"etichetta": e, "min": lo, "max": hi, "totale": 0, "pertinenti": 0} for lo, hi, e in FASCE
    ]
    regole = {"etichetta": "solo regole (senza AI)", "totale": 0, "pertinenti": 0}
    for p_ai, _p, feedback in righe:
        voce = regole if p_ai is None else next(f for f in fasce if f["min"] <= p_ai <= f["max"])
        voce["totale"] += 1
        voce["pertinenti"] += feedback != "non_pertinente"
    risultato = fasce + [regole]
    for v in risultato:
        v["percentuale"] = round(100 * v["pertinenti"] / v["totale"]) if v["totale"] else None
    return risultato
