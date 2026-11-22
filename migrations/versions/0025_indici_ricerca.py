"""Indici a trigrammi per la ricerca testuale di bandi e appalti.

Misura del 22/11/2026: la ricerca unica («srl», «ristrutturazione scuola») leggeva tutti i
6.000 bandi e 2.500 appalti, 0,5-1 secondi. L'indice GIN a trigrammi sull'espressione
minuscola e senza accenti (app/utils/ricerca.py, _colonna) serve le ricerche LIKE '%parola%'.
Serve l'estensione pg_trgm (inclusa nell'immagine PostgreSQL di produzione e della CI): dove
non c'è (es. un PostgreSQL di sviluppo senza contrib) gli indici non si creano e la ricerca
funziona come prima.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-22 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pg_trgm_disponibile() -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text("select count(*) from pg_available_extensions where name = 'pg_trgm'")
        )
    )


def upgrade() -> None:
    from app.utils.ricerca import COLONNE_INDICIZZATE, espressione_indice

    if not _pg_trgm_disponibile():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for tabella, colonna in COLONNE_INDICIZZATE:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{tabella}_{colonna}_trgm ON {tabella} "
            f"USING gin (({espressione_indice(colonna)}) gin_trgm_ops)"
        )


def downgrade() -> None:
    from app.utils.ricerca import COLONNE_INDICIZZATE

    for tabella, colonna in COLONNE_INDICIZZATE:
        op.execute(f"DROP INDEX IF EXISTS ix_{tabella}_{colonna}_trgm")
