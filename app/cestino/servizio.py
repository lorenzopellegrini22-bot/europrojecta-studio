"""Cestino: quello che si elimina resta recuperabile per 30 giorni.

Prima di eliminare un oggetto (cliente, pratica, allegato, referente…) se ne salva una copia
completa: la sua riga e tutte quelle che il database elimina con lui (le chiavi esterne con
ON DELETE CASCADE, seguite a catena), più le note della pratica o delle opportunità, che non
hanno una chiave esterna. Si annotano anche i collegamenti che l'eliminazione azzera
(ON DELETE SET NULL, es. il promemoria legato alla pratica). Il ripristino reinserisce le righe
con gli stessi id, nell'ordine delle dipendenze, e ricrea i collegamenti.

Le tabelle non sanno nulla del cestino: niente colonne «eliminato» da filtrare in ogni query.
I file degli allegati restano sul disco finché l'elemento non lascia il cestino.
"""

import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import Table, and_, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ElementoCestino, Utente

log = logging.getLogger(__name__)

GIORNI_CONSERVAZIONE = 30
# Tabelle mai copiate: dati tecnici che si rigenerano o che non appartengono all'oggetto
ESCLUSE = {"cestino", "sessione", "log_accesso", "audit_log", "novita_seguito"}


class ErroreRipristino(Exception):
    """Messaggio per il consulente: perché non si può ripristinare."""


# --- valori ↔ JSON --------------------------------------------------------------------------


def _in_json(v: Any) -> Any:
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, date):
        return {"$d": v.isoformat()}
    if isinstance(v, time):
        return {"$t": v.isoformat()}
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    if isinstance(v, bytes | bytearray | memoryview):
        return {"$b": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, timedelta):
        return {"$td": v.total_seconds()}
    return v


def _da_json(v: Any) -> Any:
    if isinstance(v, dict) and len(v) == 1:
        ((k, x),) = v.items()
        if k == "$dt":
            return datetime.fromisoformat(x)
        if k == "$d":
            return date.fromisoformat(x)
        if k == "$t":
            return time.fromisoformat(x)
        if k == "$dec":
            return Decimal(x)
        if k == "$b":
            return base64.b64decode(x)
        if k == "$td":
            return timedelta(seconds=x)
    return v


# --- raccolta ---------------------------------------------------------------------------------


def _tabelle() -> dict[str, Table]:
    return {t.name: t for t in Base.metadata.sorted_tables if t.name not in ESCLUSE}


def _chiave(t: Table, riga: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(riga[c.name] for c in t.primary_key.columns)


@dataclass
class _Raccolta:
    righe: dict[tuple[str, tuple[Any, ...]], dict[str, Any]]
    riferimenti: list[dict[str, Any]]


def _figli(db: Session, tabelle: dict[str, Table], t: Table, riga: dict[str, Any], r: _Raccolta):
    """Le righe che puntano a questa: eliminate con lei (raccolte) o scollegate (annotate)."""
    for figlia in tabelle.values():
        for fk in figlia.foreign_keys:
            if fk.column.table is not t:
                continue
            valore = riga.get(fk.column.name)
            if valore is None:
                continue
            col = fk.parent
            regola = (fk.ondelete or "").upper()
            # senza regola: se la colonna è obbligatoria l'ORM elimina i figli (cascade) o
            # l'eliminazione fallisce; se è facoltativa l'ORM la azzera
            si_elimina = regola == "CASCADE" or (regola in ("", "RESTRICT", "NO ACTION")
                                                  and not col.nullable)  # fmt: skip
            for figlio in db.execute(select(figlia).where(col == valore)).mappings():
                figlio = dict(figlio)
                if si_elimina:
                    _aggiungi(db, tabelle, figlia, figlio, r)
                elif regola in ("SET NULL", ""):
                    r.riferimenti.append(
                        {"tabella": figlia.name, "chiave": [_in_json(x) for x in
                                                            _chiave(figlia, figlio)],
                         "colonna": col.name, "valore": _in_json(valore)}
                    )  # fmt: skip


def _aggiungi(db: Session, tabelle: dict[str, Table], t: Table, riga: dict[str, Any], r: _Raccolta):
    chiave = (t.name, _chiave(t, riga))
    if chiave in r.righe:
        return
    r.righe[chiave] = riga
    _figli(db, tabelle, t, riga, r)
    # note di pratiche e opportunità: legate per tipo e id, senza chiave esterna
    if t.name in ("pratica", "opportunita"):
        tc = tabelle["commento"]
        for nota in db.execute(
            select(tc).where(tc.c.oggetto == t.name, tc.c.oggetto_id == riga["id"])
        ).mappings():
            _aggiungi(db, tabelle, tc, dict(nota), r)


def raccogli(db: Session, oggetto: Any) -> dict[str, Any]:
    """Copia dell'oggetto e di tutto ciò che si elimina con lui, pronta per il JSON."""
    db.flush()
    tabelle = _tabelle()
    t = oggetto.__table__
    pk = inspect(oggetto).identity
    riga = db.execute(
        select(t).where(and_(*(c == v for c, v in zip(t.primary_key.columns, pk, strict=True))))
    ).mappings().one()  # fmt: skip
    r = _Raccolta(righe={}, riferimenti=[])
    _aggiungi(db, tabelle, t, dict(riga), r)
    # i collegamenti verso righe che si eliminano comunque non servono
    raccolte = {(n, tuple(_in_json(x) for x in k)) for n, k in r.righe}
    riferimenti = [x for x in r.riferimenti if (x["tabella"], tuple(x["chiave"])) not in raccolte]
    return {
        "righe": [
            {"tabella": n, "valori": {c: _in_json(v) for c, v in valori.items()}}
            for (n, _k), valori in r.righe.items()
        ],
        "riferimenti": riferimenti,
    }


# --- operazioni -----------------------------------------------------------------------------


def sposta(
    db: Session,
    utente: Utente | None,
    oggetto: Any,
    tipo: str,
    descrizione: str,
    link: str | None = None,
) -> ElementoCestino:
    """Mette nel cestino la copia dell'oggetto; poi il chiamante lo elimina come prima."""
    copia = raccogli(db, oggetto)
    e = ElementoCestino(
        tipo=tipo,
        descrizione=descrizione[:300],
        tabella=oggetto.__table__.name,
        oggetto_id=inspect(oggetto).identity[0],
        link=link,
        dati=json.dumps(copia, ensure_ascii=False),
        eliminato_da_id=utente.id if utente is not None else None,
    )
    db.add(e)
    db.flush()
    return e


def ripristina(db: Session, e: ElementoCestino) -> str | None:
    """Reinserisce le righe con i loro id e ricrea i collegamenti; toglie l'elemento dal
    cestino. Restituisce la pagina da aprire. Solleva ErroreRipristino se non si può."""
    copia = json.loads(e.dati)
    tabelle = _tabelle()
    ordine = {n: i for i, n in enumerate(tabelle)}  # genitori prima dei figli
    per_tabella: dict[str, list[dict[str, Any]]] = {}
    for riga in copia["righe"]:
        valori = {c: _da_json(v) for c, v in riga["valori"].items()}
        per_tabella.setdefault(riga["tabella"], []).append(valori)
    try:
        with db.begin_nested():
            for nome in sorted(per_tabella, key=lambda n: ordine.get(n, len(ordine))):
                t = tabelle.get(nome)
                if t is None:
                    continue
                colonne = {c.name for c in t.columns}
                righe = [{c: v for c, v in r.items() if c in colonne} for r in per_tabella[nome]]
                autoriferita = [fk for fk in t.foreign_keys if fk.column.table is t]
                if autoriferita:  # es. modelli copiati: prima senza il collegamento interno
                    for r in righe:
                        db.execute(insert(t).values(**{
                            c: (None if any(fk.parent.name == c for fk in autoriferita) else v)
                            for c, v in r.items()}))  # fmt: skip
                    for r in righe:
                        for fk in autoriferita:
                            if r.get(fk.parent.name) is not None:
                                db.execute(update(t).where(*(
                                    c == r[c.name] for c in t.primary_key.columns
                                )).values({fk.parent.name: r[fk.parent.name]}))  # fmt: skip
                else:
                    db.execute(insert(t), righe)
            for rif in copia["riferimenti"]:
                t = tabelle.get(rif["tabella"])
                if t is None:
                    continue
                chiave = [_da_json(x) for x in rif["chiave"]]
                col = t.c[rif["colonna"]]
                db.execute(
                    update(t)
                    .where(*(c == v for c, v in zip(t.primary_key.columns, chiave, strict=True)))
                    .where(col.is_(None))
                    .values({col.name: _da_json(rif["valore"])})
                )
    except IntegrityError as exc:
        codice = getattr(getattr(exc, "orig", None), "sqlstate", None)
        log.info("Ripristino non riuscito per l'elemento %s del cestino: %s", e.id, exc.orig)
        if codice == "23503":
            raise ErroreRipristino(
                "Non si può ripristinare: ciò a cui apparteneva non esiste più. "
                "Se è nel cestino, ripristina prima quello."
            ) from exc
        if codice == "23505":
            raise ErroreRipristino(
                "Non si può ripristinare: nel frattempo è stato creato un elemento uguale "
                "(es. un cliente con la stessa partita IVA o un modello con lo stesso nome)."
            ) from exc
        raise ErroreRipristino("Non si può ripristinare: i dati non sono più coerenti.") from exc
    db.delete(e)
    return e.link


def file_dell_elemento(e: ElementoCestino) -> list[tuple[str, str]]:
    """(tabella, percorso) dei file salvati dall'app (allegati, PDF dei bandi) nell'elemento."""
    copia = json.loads(e.dati)
    return [
        (r["tabella"], r["valori"]["percorso"])
        for r in copia["righe"]
        if r["tabella"] in ("allegato", "documento_bando") and r["valori"].get("percorso")
    ]


def _elimina_file(db: Session, tabella: str, percorso: str) -> None:
    """Toglie il file dal disco se nessuna riga rimasta lo usa (lo stesso PDF può servire a
    più bandi)."""
    t = _tabelle()[tabella]
    if db.scalar(select(t.c.id).where(t.c.percorso == percorso).limit(1)) is not None:
        return
    if tabella == "allegato":
        from app.pratiche.allegati import elimina_file

        elimina_file(percorso)
        return
    import contextlib

    from app.documenti import servizio as documenti

    base = documenti.cartella().resolve()
    file = (base / percorso).resolve()
    if file.is_relative_to(base):
        with contextlib.suppress(OSError):
            file.unlink(missing_ok=True)
            for copia in file.parent.glob(f"{file.stem}.*.docx"):
                copia.unlink(missing_ok=True)


def elimina_definitivamente(db: Session, e: ElementoCestino) -> None:
    db.delete(e)
    db.flush()
    for tabella, percorso in file_dell_elemento(e):
        _elimina_file(db, tabella, percorso)


def svuota_scaduti(db: Session, adesso: datetime | None = None) -> int:
    """Elementi nel cestino da più di 30 giorni: eliminati per sempre (job notturno)."""
    limite = (adesso or datetime.now(UTC)) - timedelta(days=GIORNI_CONSERVAZIONE)
    scaduti = list(db.scalars(select(ElementoCestino).where(ElementoCestino.creato_il < limite)))
    for e in scaduti:
        elimina_definitivamente(db, e)
    return len(scaduti)


def scade_il(e: ElementoCestino) -> date:
    return (e.creato_il + timedelta(days=GIORNI_CONSERVAZIONE)).date()


def con_avviso_id(url: str, eid: int) -> str:
    """Aggiunge ?cestino=<id> all'indirizzo di ritorno (prima dell'ancora #...)."""
    parti = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parti.query) if k != "cestino"]
    query.append(("cestino", str(eid)))
    return urlunsplit(parti._replace(query=urlencode(query)))


def con_avviso(url: str, e: ElementoCestino) -> str:
    """Aggiunge ?cestino=<id> all'indirizzo di ritorno: la pagina mostra «Eliminato · Annulla»."""
    parti = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parti.query) if k != "cestino"]
    query.append(("cestino", str(e.id)))
    return urlunsplit(parti._replace(query=urlencode(query)))
