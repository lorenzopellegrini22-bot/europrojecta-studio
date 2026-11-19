# ruff: noqa: E501
"""Ricerca unica (casella in alto nel menu): clienti, pratiche, bandi e gare in una pagina.
Si cerca per nome, P.IVA, codice fiscale, codice pratica, protocollo, CIG o numero TED.
Ogni gruppo compare solo a chi ha la sezione corrispondente."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.permessi import puo_vedere
from app.models import Appalto, Azienda, Bando, Pratica, Utente
from app.utils.ricerca import condizione, pertinenza

PER_GRUPPO = 8


@dataclass
class Gruppo:
    titolo: str
    tutti: str  # link alla lista completa con la stessa ricerca
    voci: list[dict[str, Any]] = field(default_factory=list)


def _codice(q: str) -> str:
    return "".join(q.split()).upper()


def cerca(db: Session, utente: Utente, q: str) -> list[Gruppo]:
    q = q.strip()[:200]
    if len(q) < 2:
        return []
    from urllib.parse import quote

    cq = quote(q)
    codice = _codice(q)
    gruppi: list[Gruppo] = []

    if puo_vedere(utente, "/clienti"):
        g = Gruppo("Clienti", f"/clienti?q={cq}")
        cond = condizione(q, Azienda.ragione_sociale)
        filtri = [Azienda.partita_iva == codice, Azienda.codice_fiscale == codice]
        if cond is not None:
            filtri.append(cond)
        for a in db.scalars(
            select(Azienda).where(or_(*filtri)).order_by(Azienda.ragione_sociale).limit(PER_GRUPPO)
        ):
            g.voci.append(
                {
                    "titolo": a.ragione_sociale,
                    "dettaglio": " · ".join(x for x in (a.partita_iva and f"P.IVA {a.partita_iva}", a.stato_cliente) if x),
                    "url": f"/clienti/{a.id}",
                }
            )  # fmt: skip
        gruppi.append(g)

    if puo_vedere(utente, "/pratiche"):
        g = Gruppo("Pratiche", f"/pratiche?q={cq}")
        cond = condizione(q, Azienda.ragione_sociale, Pratica.bando_testo, Bando.titolo)
        filtri = [Pratica.codice == codice, Pratica.protocollo == q]
        if cond is not None:
            filtri.append(cond)
        for p in db.scalars(
            select(Pratica)
            .join(Azienda, Pratica.azienda_id == Azienda.id)
            .outerjoin(Bando, Pratica.bando_id == Bando.id)
            .where(or_(*filtri))
            .options(selectinload(Pratica.azienda), selectinload(Pratica.bando))
            .order_by(Pratica.id.desc())
            .limit(PER_GRUPPO)
        ):
            g.voci.append(
                {
                    "titolo": f"{p.codice} · {p.azienda.ragione_sociale}",
                    "dettaglio": p.nome_bando,
                    "url": f"/pratiche/{p.id}",
                }
            )
        gruppi.append(g)

    if puo_vedere(utente, "/bandi"):
        g = Gruppo("Bandi", f"/bandi?q={cq}")
        cond = condizione(q, Bando.titolo, Bando.ente)
        if cond is not None:
            ordine = pertinenza(q, Bando.titolo)
            for b in db.scalars(
                select(Bando)
                .where(cond, Bando.bando_canonico_id.is_(None))
                .order_by(ordine.desc(), Bando.data_chiusura.desc().nulls_last())
                .limit(PER_GRUPPO)
            ):
                scad = f"chiude il {b.data_chiusura:%d/%m/%Y}" if b.data_chiusura else ""
                g.voci.append(
                    {
                        "titolo": b.titolo,
                        "dettaglio": " · ".join(x for x in (b.ente, b.stato.replace("_", " "), scad) if x),
                        "url": f"/bandi/{b.id}",
                    }
                )  # fmt: skip
        gruppi.append(g)

    if puo_vedere(utente, "/appalti"):
        g = Gruppo("Gare d'appalto", f"/appalti?q={cq}")
        cond = condizione(q, Appalto.titolo, Appalto.stazione_appaltante)
        filtri = [Appalto.cig == codice, Appalto.id_esterno == q.strip()]
        if cond is not None:
            filtri.append(cond)
        for a in db.scalars(
            select(Appalto)
            .where(or_(*filtri))
            .order_by(Appalto.scadenza.desc().nulls_last())
            .limit(PER_GRUPPO)
        ):
            scad = f"scade il {a.scadenza:%d/%m/%Y}" if a.scadenza else ""
            g.voci.append(
                {
                    "titolo": a.titolo,
                    "dettaglio": " · ".join(x for x in (a.cig and f"CIG {a.cig}", a.stazione_appaltante, scad) if x),
                    "url": f"/appalti/{a.id}",
                }
            )  # fmt: skip
        gruppi.append(g)
    return gruppi
