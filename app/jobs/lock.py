"""Advisory lock di PostgreSQL: impedisce che due esecuzioni dello stesso job si sovrappongano.

Il lock è legato alla connessione: se il processo muore, Postgres lo rilascia da solo.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text


def chiave_lock(nome: str) -> int:
    """Converte il nome del job in un intero a 64 bit con segno (il tipo dei lock di Postgres)."""
    digest = hashlib.sha256(nome.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def lock_job(engine: Engine, nome: str) -> Iterator[bool]:
    """Prova a prendere il lock senza attendere.

    Restituisce True se il lock è stato preso (il job può partire), False se un'altra
    esecuzione è già in corso. Il lock si rilascia all'uscita dal blocco `with`.
    """
    chiave = chiave_lock(nome)
    with engine.connect() as conn:
        preso = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": chiave}).scalar())
        try:
            yield preso
        finally:
            if preso:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": chiave})
