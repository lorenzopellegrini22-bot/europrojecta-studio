"""Note interne con @menzioni dei colleghi.

Si cita un collega con @ seguito dal suo nome (es. @giulia) o dalla prima parte della sua
email (es. @g.rossi). Chi è citato vede la nota in «Oggi» e riceve un'email (se collegata).
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Commento, Menzione, Utente
from app.utils.ricerca import normalizza

log = logging.getLogger(__name__)

MAX_TESTO = 4000
_CITAZIONE = re.compile(r"(?<![\w.])@([\w][\w.\-]*)")


def nomi_per_citare(utente: Utente) -> set[str]:
    """Come si può citare un utente: nome di battesimo e parte dell'email prima della @."""
    nomi = {normalizza(utente.email.split("@", 1)[0])}
    if utente.nome.strip():
        nomi.add(normalizza(utente.nome.split()[0]))
        nomi.add(normalizza(utente.nome.replace(" ", "")))
    return {n for n in nomi if n}


def citazione(utente: Utente) -> str:
    return "@" + normalizza(utente.nome.split()[0] if utente.nome.strip() else utente.email)


def citati(db: Session, testo: str, autore: Utente | None) -> list[Utente]:
    parole = {normalizza(m.rstrip(".-")) for m in _CITAZIONE.findall(testo)}
    if not parole:
        return []
    trovati = []
    for u in db.scalars(select(Utente).where(Utente.attivo).order_by(Utente.id)):
        if autore is not None and u.id == autore.id:
            continue
        if nomi_per_citare(u) & parole:
            trovati.append(u)
    return trovati


def aggiungi(
    db: Session, oggetto: str, oggetto_id: int, testo: str, autore: Utente, link: str
) -> Commento:
    testo = testo.strip()[:MAX_TESTO]
    c = Commento(oggetto=oggetto, oggetto_id=oggetto_id, testo=testo, utente_id=autore.id)
    db.add(c)
    persone = citati(db, testo, autore)
    for u in persone:
        c.menzioni.append(Menzione(utente_id=u.id))
    db.flush()
    _avvisa(db, persone, autore, testo, link)
    return c


def _avvisa(db: Session, persone: list[Utente], autore: Utente, testo: str, link: str) -> None:
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.config import get_settings

    if not persone or not email_disponibile(db):
        return
    indirizzo = get_settings().url_pubblico.rstrip("/") + link
    for u in persone:
        try:
            invia_registrando(
                db,
                tipo="avviso",
                destinatario=u.email,
                oggetto=f"{autore.nome} ti ha citato in una nota",
                testo=f"{autore.nome} ha scritto:\n\n{testo[:1500]}\n\nApri: {indirizzo}",
                utente=u,
            )
        except Exception:  # l'email è un aiuto: la nota resta comunque
            log.exception("Avviso di menzione non inviato")


def elenco(db: Session, oggetto: str, oggetto_id: int) -> list[Commento]:
    return list(
        db.scalars(
            select(Commento)
            .where(Commento.oggetto == oggetto, Commento.oggetto_id == oggetto_id)
            .options(selectinload(Commento.utente))
            .order_by(Commento.creato_il)
        )
    )


def conteggi(db: Session, oggetto: str, ids: list[int]) -> dict[int, int]:
    from sqlalchemy import func

    if not ids:
        return {}
    return dict(
        db.execute(
            select(Commento.oggetto_id, func.count())
            .where(Commento.oggetto == oggetto, Commento.oggetto_id.in_(ids))
            .group_by(Commento.oggetto_id)
        ).all()
    )


def segna_lette(db: Session, utente: Utente, oggetto: str, oggetto_id: int) -> None:
    """Aprendo la pratica o l'opportunità le menzioni dell'utente lì dentro sono lette."""
    ora = datetime.now(UTC)
    for m in db.scalars(
        select(Menzione)
        .join(Commento)
        .where(
            Menzione.utente_id == utente.id,
            Menzione.letta_il.is_(None),
            Commento.oggetto == oggetto,
            Commento.oggetto_id == oggetto_id,
        )
    ):
        m.letta_il = ora


def menzioni_da_leggere(db: Session, utente: Utente) -> list[Menzione]:
    return list(
        db.scalars(
            select(Menzione)
            .where(Menzione.utente_id == utente.id, Menzione.letta_il.is_(None))
            .options(selectinload(Menzione.commento).selectinload(Commento.utente))
            .order_by(Menzione.id.desc())
            .limit(30)
        )
    )


def link(oggetto: str, oggetto_id: int, db: Session | None = None) -> str:
    if oggetto == "pratica":
        return f"/pratiche/{oggetto_id}#note"
    return f"/opportunita/{oggetto_id}#note"
