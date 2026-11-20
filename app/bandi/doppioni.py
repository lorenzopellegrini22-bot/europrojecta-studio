"""Bandi doppi tra la fonte nazionale (Incentivi Italia) e le fonti regionali.

Vale anche per Invitalia: le misure che gestisce (ON, Smart&Start, Nuova Sabatini…) sono
anche nel dataset nazionale, registrate con l'ente Ministero delle Imprese o Invitalia; i titoli
di Invitalia sono brevi, quindi basta che tutte le loro parole compaiano nel titolo nazionale.

Lo stesso bando regionale può arrivare due volte: dal portale della Regione e dal dataset
nazionale. Si tiene come principale quello della fonte regionale (più aggiornata, con i PDF)
e il nazionale viene collegato con `bando_canonico_id`: non compare più nelle liste, nelle
opportunità e nell'estrazione AI, ma resta consultabile dalla scheda.

Regole (misurate sui dati del 18/11/2026: su 44 bandi regionali aperti, 2 doppioni veri;
i titoli simili sotto la soglia erano bandi diversi di altre Regioni):
- titoli simili: indice di Jaccard sulle parole significative >= 0,6;
- stessa Regione: l'ente o le regioni del bando nazionale contengono quella della fonte;
- scadenze compatibili: se entrambe note, distanti al massimo 7 giorni;
- stessa pagina ufficiale (dominio e ultima parte dell'indirizzo, es. «.../minibond» e
  «.../bando/minibond/»): basta una somiglianza dei titoli >= 0,3. Il 20/11/2026 «Bando 1.3.2 –
  Sostegno alle PMI (Minibond)» di Sviluppo Toscana compariva anche come «PR Toscana FESR
  2021-2027 - 1.3.2.1 Sostegno alle PMI…» (somiglianza 0,42); con la soglia bassa ma senza la
  stessa pagina si sarebbero uniti bandi diversi (es. due misure «Basket Bond Lazio»).
"""

import re
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Bando, Fonte
from app.utils.ricerca import normalizza
from app.utils.territorio import REGIONI

FONTE_NAZIONALE = "incentivi_italia"
FONTE_INVITALIA = "invitalia"
# enti con cui Incentivi Italia registra le misure che Invitalia gestisce
ENTI_INVITALIA = ("invitalia", "ministero delle imprese", "sviluppo economico")
SOGLIA = 0.6
SOGLIA_STESSA_PAGINA = 0.3
GIORNI_SCADENZA = 7
VUOTE = {
    "di", "e", "per", "la", "il", "lo", "del", "della", "delle", "dei", "degli", "a", "in",
    "al", "alle", "ai", "le", "i", "gli", "con", "da", "su", "bando", "avviso", "pubblico",
    "anno", "edizione", "ed",
}  # fmt: skip


def parole(titolo: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]+", normalizza(titolo or ""))
        if w not in VUOTE and (len(w) > 2 or w.isdigit())
    }


def somiglianza(a: str, b: str) -> float:
    pa, pb = parole(a), parole(b)
    return len(pa & pb) / len(pa | pb) if pa and pb else 0.0


def pagina(link: str | None) -> tuple[str, str] | None:
    """(dominio, ultima parte dell'indirizzo) della pagina ufficiale, per confrontare i link
    scritti in modo diverso dalle due fonti."""
    if not link:
        return None
    parti = urlsplit(link.strip())
    segmenti = [s for s in parti.path.lower().split("/") if s and s not in ("bando", "bandi")]
    if not parti.netloc or not segmenti:
        return None
    return parti.netloc.lower().removeprefix("www."), segmenti[-1]


def _gestito_da_invitalia(nazionale: Bando) -> bool:
    ente = normalizza(nazionale.ente or "")
    link = (nazionale.link_ufficiale or "").lower()
    return any(x in ente for x in ENTI_INVITALIA) or "invitalia.it" in link


def regione_della_fonte(fonte: Fonte) -> str | None:
    nome = normalizza(f"{fonte.nome} {fonte.codice.replace('_', ' ')}")
    for r in sorted(REGIONI, key=len, reverse=True):
        if normalizza(r.split("/")[0]) in nome:
            return r
    return None


def _stessa_regione(nazionale: Bando, regione: str) -> bool:
    nome = normalizza(regione.split("/")[0])
    if nome in normalizza(nazionale.ente or ""):
        return True
    r = nazionale.requisiti
    return bool(r and r.regioni and regione in r.regioni)


def _scadenze_compatibili(a: Bando, b: Bando) -> bool:
    if a.data_chiusura is None or b.data_chiusura is None:
        return True
    return abs((a.data_chiusura - b.data_chiusura).days) <= GIORNI_SCADENZA


def collega(db: Session) -> str:
    """Ricalcola i collegamenti tra i bandi aperti o in arrivo. Idempotente."""
    fonti = {f.id: f for f in db.scalars(select(Fonte))}
    aperti = list(
        db.scalars(
            select(Bando)
            .where(Bando.stato.in_(("aperto", "in_arrivo")))
            .options(selectinload(Bando.requisiti))
        )
    )
    nazionali = [b for b in aperti if fonti[b.fonte_id].codice == FONTE_NAZIONALE]
    regionali = [b for b in aperti if fonti[b.fonte_id].codice != FONTE_NAZIONALE]
    parole_naz = {b.id: parole(b.titolo) for b in nazionali}
    collegati: dict[int, int] = {}
    for reg in regionali:
        invitalia = fonti[reg.fonte_id].codice == FONTE_INVITALIA
        regione = regione_della_fonte(fonti[reg.fonte_id])
        if regione is None and not invitalia:
            continue
        pr = parole(reg.titolo)
        if not pr:
            continue
        pagina_reg = pagina(reg.link_ufficiale)
        migliore, punteggio = None, 0.0
        for naz in nazionali:
            pn = parole_naz[naz.id]
            if not pn or naz.id in collegati:
                continue
            s = len(pr & pn) / len(pr | pn)
            stessa_pagina = pagina_reg is not None and pagina_reg == pagina(naz.link_ufficiale)
            # i titoli di Invitalia sono brevi («Smart&Start Italia»): basta che le loro parole
            # siano tutte nel titolo nazionale («Smart&Start Italia - Sostegno alle startup…»)
            contenuto = invitalia and len(pr) >= 2 and pr <= pn and s >= SOGLIA_STESSA_PAGINA
            if s < (SOGLIA_STESSA_PAGINA if stessa_pagina else SOGLIA) and not contenuto:
                continue
            if invitalia and not _gestito_da_invitalia(naz):
                continue
            if not invitalia and not _stessa_regione(naz, regione):  # type: ignore[arg-type]
                continue
            # a parità, vince il bando nazionale con la stessa pagina ufficiale
            voto = s + (1 if stessa_pagina else 0)
            if voto > punteggio and _scadenze_compatibili(reg, naz):
                migliore, punteggio = naz, voto
        if migliore is not None:
            collegati[migliore.id] = reg.id
    # Toglie i collegamenti non più validi, poi scrive quelli nuovi
    tolti = 0
    for b in nazionali:
        if b.bando_canonico_id is not None and b.id not in collegati:
            b.bando_canonico_id = None
            tolti += 1
    for naz_id, reg_id in collegati.items():
        db.execute(update(Bando).where(Bando.id == naz_id).values(bando_canonico_id=reg_id))
    db.commit()
    return f"Bandi doppi collegati: {len(collegati)}" + (f", scollegati {tolti}" if tolti else "")
