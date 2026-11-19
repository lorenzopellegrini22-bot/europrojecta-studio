"""Bandi e gare seguiti: ogni mattina si confronta lo stato di oggi con quello salvato e ogni
differenza (scadenza, stato, documenti nuovi come FAQ e chiarimenti, testo della fonte) diventa
una novità per chi segue, visibile in «Oggi» e nell'email del mattino."""

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appalto, Bando, DocumentoBando, NovitaSeguito, Seguito, Utente

STATI = {"aperto": "aperto", "in_arrivo": "in arrivo", "chiuso": "chiuso", "scaduto": "scaduto"}


def _data(d: Any) -> str | None:
    return d.isoformat() if d else None


def istantanea_bando(db: Session, b: Bando) -> dict[str, Any]:
    documenti = sorted(
        (d.titolo or d.url, d.url)
        for d in db.scalars(select(DocumentoBando).where(DocumentoBando.bando_id == b.id))
        if not d.url.startswith("caricato-a-mano:")
    )
    return {
        "scadenza": _data(b.data_chiusura),
        "apertura": _data(b.data_apertura),
        "stato": b.stato,
        "documenti": [list(x) for x in documenti],
        "testo": b.hash_contenuto,
    }


def istantanea_gara(a: Appalto) -> dict[str, Any]:
    from app.appalti.documenti import documentazione

    documenti = sorted((d.get("titolo") or d["url"], d["url"]) for d in documentazione(a))
    testo = hashlib.sha256(f"{a.titolo}\n{a.descrizione or ''}".encode()).hexdigest()
    return {
        "scadenza": _data(a.scadenza),
        "importo": str(a.importo_stimato) if a.importo_stimato is not None else None,
        "documenti": [list(x) for x in documenti],
        "testo": testo,
    }


def _it(data: str | None) -> str:
    return datetime.fromisoformat(data).strftime("%d/%m/%Y") if data else "non indicata"


def differenze(prima: dict[str, Any], dopo: dict[str, Any]) -> list[str]:
    cambi: list[str] = []
    if prima.get("scadenza") != dopo.get("scadenza"):
        cambi.append(
            f"Scadenza cambiata: {_it(prima.get('scadenza'))} → {_it(dopo.get('scadenza'))}"
        )
    if "apertura" in dopo and prima.get("apertura") != dopo.get("apertura"):
        cambi.append(
            f"Apertura cambiata: {_it(prima.get('apertura'))} → {_it(dopo.get('apertura'))}"
        )
    if "stato" in dopo and prima.get("stato") != dopo.get("stato"):
        cambi.append(f"Ora è {STATI.get(dopo['stato'], dopo['stato'])}")
    if "importo" in dopo and prima.get("importo") != dopo.get("importo"):
        cambi.append(
            f"Importo cambiato: {prima.get('importo') or '—'} → {dopo.get('importo') or '—'}"
        )
    url_prima = {u for _t, u in prima.get("documenti") or []}
    nuovi = [t for t, u in dopo.get("documenti") or [] if u not in url_prima]
    if nuovi:
        from app.documenti.documentazione import pulisci

        elenco = ", ".join(pulisci(t, 60) for t in nuovi[:5])
        altri = f" e altri {len(nuovi) - 5}" if len(nuovi) > 5 else ""
        cambi.append(f"Nuovi documenti: {elenco}{altri}")
    if prima.get("testo") != dopo.get("testo") and not cambi:
        cambi.append("La fonte ha aggiornato la scheda (testo o condizioni): ricontrolla")
    return cambi


def segui(
    db: Session, utente: Utente, bando: Bando | None = None, gara: Appalto | None = None
) -> Seguito:
    q = select(Seguito).where(Seguito.utente_id == utente.id)
    q = q.where(Seguito.bando_id == bando.id) if bando else q.where(Seguito.appalto_id == gara.id)  # type: ignore[union-attr]
    s = db.scalar(q)
    if s is None:
        s = Seguito(
            utente_id=utente.id,
            bando_id=bando.id if bando else None,
            appalto_id=gara.id if gara else None,
            istantanea=istantanea_bando(db, bando) if bando else istantanea_gara(gara),  # type: ignore[arg-type]
        )
        db.add(s)
    return s


def smetti(db: Session, utente: Utente, bando_id: int | None, appalto_id: int | None) -> None:
    q = select(Seguito).where(Seguito.utente_id == utente.id)
    q = (
        q.where(Seguito.bando_id == bando_id)
        if bando_id
        else q.where(Seguito.appalto_id == appalto_id)
    )
    s = db.scalar(q)
    if s is not None:
        db.delete(s)


def segue(
    db: Session, utente: Utente, bando_id: int | None = None, appalto_id: int | None = None
) -> bool:
    q = select(Seguito.id).where(Seguito.utente_id == utente.id)
    q = (
        q.where(Seguito.bando_id == bando_id)
        if bando_id
        else q.where(Seguito.appalto_id == appalto_id)
    )
    return db.scalar(q) is not None


def controlla(db: Session) -> str:
    """Ogni mattina, prima dell'email: novità per chi segue bandi e gare."""
    nuove = 0
    for s in db.scalars(select(Seguito)):
        if s.bando_id:
            b = db.get(Bando, s.bando_id)
            adesso = istantanea_bando(db, b) if b else None
        else:
            a = db.get(Appalto, s.appalto_id)
            adesso = istantanea_gara(a) if a else None
        if adesso is None:
            continue
        cambi = differenze(s.istantanea or {}, adesso)
        if cambi:
            for testo in cambi:
                db.add(NovitaSeguito(seguito_id=s.id, testo=testo))
            nuove += len(cambi)
        s.istantanea = adesso
    db.commit()
    return f"Bandi e gare seguiti: {nuove} novità."


def novita(
    db: Session, utente: Utente, solo_da_leggere: bool = True, limite: int = 30
) -> list[tuple[NovitaSeguito, str, str]]:
    """[(novità, titolo, link)] dell'utente, le più recenti prima."""
    q = (
        select(NovitaSeguito, Seguito)
        .join(Seguito)
        .where(Seguito.utente_id == utente.id)
        .order_by(NovitaSeguito.id.desc())
        .limit(limite)
    )
    if solo_da_leggere:
        q = q.where(NovitaSeguito.letta_il.is_(None))
    risultato = []
    for n, s in db.execute(q).all():
        if s.bando_id:
            b = db.get(Bando, s.bando_id)
            risultato.append((n, b.titolo if b else "Bando", f"/bandi/{s.bando_id}"))
        else:
            a = db.get(Appalto, s.appalto_id)
            risultato.append((n, a.titolo if a else "Gara", f"/appalti/{s.appalto_id}"))
    return risultato


def segna_lette(
    db: Session, utente: Utente, bando_id: int | None = None, appalto_id: int | None = None
) -> None:
    """Aprendo la scheda, le novità di quel bando o gara sono lette."""
    q = (
        select(NovitaSeguito)
        .join(Seguito)
        .where(Seguito.utente_id == utente.id, NovitaSeguito.letta_il.is_(None))
    )
    q = (
        q.where(Seguito.bando_id == bando_id)
        if bando_id
        else q.where(Seguito.appalto_id == appalto_id)
    )
    ora = datetime.now(UTC)
    for n in db.scalars(q):
        n.letta_il = ora
