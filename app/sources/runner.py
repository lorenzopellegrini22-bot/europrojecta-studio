"""Runner comune delle fonti: scarica → interpreta → confronta → salva.

Regole:
- se la versione remota (Last-Modified) è quella già elaborata, non si riscarica: si
  ricalcolano solo gli stati (le date passano anche senza file nuovi);
- controllo di sanità: se il file ha meno dell'80% dei record della versione precedente il
  run si ferma SENZA toccare i bandi (un download troncato non deve "chiudere" centinaia di
  bandi);
- confronto record per record con l'hash del contenuto; a ogni modifica si salva la
  versione precedente in bando_versione;
- un bando assente da 2 versioni consecutive del file viene marcato come scomparso (chiuso);
- se l'ID della fonte cambia ma l'ID RNA è lo stesso, il bando viene riconciliato;
- ogni esecuzione è registrata in fonte_run; i fallimenti consecutivi sono contati sulla fonte.
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.bandi.stato import calcola_stato
from app.models import Bando, BandoVersione, Fonte, FonteRun
from app.sources import http
from app.sources.base import Fonte as FonteImpl
from app.sources.base import RecordBando, RequisitiFonte

log = logging.getLogger(__name__)

SOGLIA_SANITA = 0.8
VERSIONI_ASSENTI_PER_SCOMPARSA = 2

CAMPI_REQUISITI_FONTE = (
    "territorio",
    "regioni",
    "comuni",
    "aree_speciali",
    "dimensioni",
    "ateco_ammessi",
    "ateco_versione",
    "tipologie_soggetto",
    "settori",
    "forma_agevolazione",
    "finalita",
    "importo_min",
    "importo_max",
    "spesa_min",
    "spesa_max",
    "dotazione",
)


def _adesso() -> datetime:
    return datetime.now(UTC)


def hash_contenuto(raw: dict[str, Any]) -> str:
    testo = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


def assicura_fonte(db: Session, impl: FonteImpl) -> Fonte:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == impl.codice))
    if fonte is None:
        fonte = Fonte(codice=impl.codice, nome=impl.nome, url=impl.url)
        db.add(fonte)
        db.commit()
    return fonte


_AI: dict[int, dict] = {}  # type: ignore[type-arg]
_MANUALI: dict[int, dict] = {}  # type: ignore[type-arg]


def scrivi_requisiti(bando: Bando, rf: RequisitiFonte) -> None:
    """Salva i valori della fonte e risolve i requisiti con le estrazioni AI e le correzioni
    manuali già presenti (correzione manuale > fonte > AI, vedi app/extraction/)."""
    from app.extraction.applica import scrivi, valori_da_fonte

    scrivi(
        bando,
        valori_da_fonte(rf),
        _AI.get(bando.id) if bando.id else None,
        _MANUALI.get(bando.id) if bando.id else None,
    )


def _applica_campi(bando: Bando, rec: RecordBando, h: str) -> None:
    bando.id_esterno = rec.id_esterno
    bando.id_rna = rec.id_rna
    bando.titolo = rec.titolo
    bando.ente = rec.ente
    bando.descrizione = rec.descrizione
    bando.link_ufficiale = rec.link_ufficiale
    bando.link_domande = rec.link_domande
    bando.data_apertura = rec.data_apertura
    bando.data_chiusura = rec.data_chiusura
    bando.note_apertura_chiusura = rec.note_apertura_chiusura
    bando.stato_fonte = rec.stato_fonte
    bando.raw = rec.raw
    bando.hash_contenuto = h
    scrivi_requisiti(bando, rec.requisiti)


def aggiorna_stati(bandi: list[Bando], oggi: date) -> int:
    """Ricalcola lo stato di tutti i bandi; restituisce quanti sono passati a "chiuso"."""
    chiusi = 0
    for b in bandi:
        nuovo = calcola_stato(
            b.data_apertura,
            b.data_chiusura,
            oggi,
            scomparso=b.scomparso_dal_feed_il is not None,
            stato_fonte=b.stato_fonte,
        )
        if nuovo != b.stato:
            if nuovo == "chiuso":
                chiusi += 1
            b.stato = nuovo
    return chiusi


def applica_record(
    db: Session,
    fonte: Fonte,
    records: list[RecordBando],
    run: FonteRun,
    oggi: date,
    ririsolvi: bool = False,
    assenze_per_scomparsa: int = VERSIONI_ASSENTI_PER_SCOMPARSA,
) -> None:
    """Con ririsolvi=True i requisiti si ricalcolano anche per i record invariati
    (serve dopo una correzione del parser)."""
    esistenti: dict[str, Bando] = {
        b.id_esterno: b
        for b in db.scalars(
            select(Bando).where(Bando.fonte_id == fonte.id).options(selectinload(Bando.requisiti))
        )
    }
    from app.extraction.applica import carica_ai_e_manuali

    ai, manuali = carica_ai_e_manuali(db)
    _AI.clear()
    _AI.update(ai)
    _MANUALI.clear()
    _MANUALI.update(manuali)
    conta_rna = Counter(b.id_rna for b in esistenti.values() if b.id_rna)
    per_rna = {b.id_rna: b for b in esistenti.values() if b.id_rna and conta_rna[b.id_rna] == 1}
    ids_nel_file = {r.id_esterno for r in records}

    visti: set[int] = set()
    elaborati: set[str] = set()
    duplicati = riconciliati = 0
    ora = _adesso()

    for rec in records:
        if rec.id_esterno in elaborati:
            duplicati += 1
            continue
        elaborati.add(rec.id_esterno)
        h = hash_contenuto(rec.raw)
        bando = esistenti.get(rec.id_esterno)

        # ID della fonte reimpostato: stesso ID RNA, vecchio ID non più presente nel file
        if bando is None and rec.id_rna and rec.id_rna in per_rna:
            candidato = per_rna[rec.id_rna]
            if candidato.id_esterno not in ids_nel_file and candidato.id not in visti:
                bando = candidato
                riconciliati += 1

        if bando is None:
            bando = Bando(fonte_id=fonte.id, visto_la_prima_volta=ora, stato="sconosciuto")
            _applica_campi(bando, rec, h)
            bando.stato = calcola_stato(
                bando.data_apertura, bando.data_chiusura, oggi, stato_fonte=bando.stato_fonte
            )
            db.add(bando)
            run.nuovi += 1
        elif bando.hash_contenuto != h or bando.id_esterno != rec.id_esterno:
            db.add(
                BandoVersione(bando_id=bando.id, raw=bando.raw, hash_contenuto=bando.hash_contenuto)
            )
            _applica_campi(bando, rec, h)
            run.modificati += 1
        else:
            run.invariati += 1
            # record uguale ma letto meglio (es. date ricavate dalle note): si aggiornano
            bando.data_apertura = rec.data_apertura
            bando.data_chiusura = rec.data_chiusura
            if ririsolvi:
                scrivi_requisiti(bando, rec.requisiti)

        if bando.scomparso_dal_feed_il is not None:
            bando.scomparso_dal_feed_il = None  # ricomparso
        bando.versioni_assenti = 0
        if bando.id is not None:
            visti.add(bando.id)

    db.flush()
    tutti = list(db.scalars(select(Bando).where(Bando.fonte_id == fonte.id)))
    for b in tutti:
        if b.id in visti or b.id_esterno in elaborati or b.scomparso_dal_feed_il is not None:
            continue
        b.versioni_assenti += 1
        if b.versioni_assenti >= assenze_per_scomparsa:
            b.scomparso_dal_feed_il = ora
            run.scomparsi += 1

    run.chiusi = aggiorna_stati(tutti, oggi)
    run.errori = duplicati
    run.dettagli = {
        "duplicati_nel_file": duplicati,
        "riconciliati_per_id_rna": riconciliati,
        "totale_bandi_fonte": len(tutti),
    }


def esegui_raccolta(
    db: Session,
    impl: FonteImpl,
    oggi: date | None = None,
    forza: bool = False,
    fabbrica_client: Callable[[], httpx.Client] = http.client,
) -> FonteRun:
    oggi = oggi or date.today()
    fonte = assicura_fonte(db, impl)
    run = FonteRun(fonte_id=fonte.id, esito="in_corso")
    db.add(run)
    db.commit()
    run_id, fonte_id = run.id, fonte.id

    try:
        with fabbrica_client() as client:
            versione = impl.versione_remota(client)
            if not forza and versione and versione == fonte.ultimo_last_modified:
                tutti = list(db.scalars(select(Bando).where(Bando.fonte_id == fonte.id)))
                run.chiusi = aggiorna_stati(tutti, oggi)
                run.esito = "invariato"
                run.messaggio = f"File invariato (versione {versione}): stati ricalcolati."
                fonte.ultimo_successo = _adesso()
                fonte.fallimenti_consecutivi = 0
                run.fine = _adesso()
                db.commit()
                return run
            percorso = impl.scarica(client)
        try:
            records = impl.interpreta(percorso)
        finally:
            percorso.unlink(missing_ok=True)

        run.record_letti = len(records)
        precedente = fonte.ultimo_conteggio
        soglia = getattr(impl, "soglia_sanita", SOGLIA_SANITA)
        if precedente and (not records or len(records) < soglia * precedente):
            run.esito = "fermato_sanita"
            run.messaggio = (
                f"Il file contiene {len(records)} record contro {precedente} della versione "
                f"precedente (meno dell'{int(soglia * 100)}%): nessun bando modificato."
            )
            fonte.fallimenti_consecutivi += 1
        else:
            applica_record(
                db,
                fonte,
                records,
                run,
                oggi,
                ririsolvi=forza,
                assenze_per_scomparsa=getattr(
                    impl, "assenze_per_scomparsa", VERSIONI_ASSENTI_PER_SCOMPARSA
                ),
            )
            fonte.ultimo_last_modified = versione
            fonte.ultimo_conteggio = len(records)
            fonte.ultimo_successo = _adesso()
            fonte.fallimenti_consecutivi = 0
            run.esito = "ok"
            run.messaggio = (
                f"{run.nuovi} nuovi, {run.modificati} modificati, {run.invariati} invariati, "
                f"{run.chiusi} chiusi, {run.scomparsi} scomparsi."
            )
        run.fine = _adesso()
        db.commit()
    except Exception as e:  # noqa: BLE001 — qualsiasi errore va registrato, non deve fermare il worker
        log.exception("Raccolta %s fallita", impl.codice)
        db.rollback()
        run = db.get(FonteRun, run_id)  # type: ignore[assignment]
        fonte = db.get(Fonte, fonte_id)  # type: ignore[assignment]
        run.esito = "errore"
        run.messaggio = f"{type(e).__name__}: {e}"[:2000]
        run.fine = _adesso()
        fonte.fallimenti_consecutivi += 1
        db.commit()
    return run
