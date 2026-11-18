"""Appalti: scheda completa (ente, procedura, criterio, documenti, categorie SOA) e riassunto.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("appalto", sa.Column("cig", sa.String(length=20), nullable=True))
    op.add_column("appalto", sa.Column("tipo_procedura", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("criterio_aggiudicazione", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("descrizione", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("riferimenti_legge", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("luogo_esecuzione", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("ente_indirizzo", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("ente_email", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("ente_telefono", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("ente_sito", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("link_partecipazione", sa.Text(), nullable=True))
    op.add_column(
        "appalto", sa.Column("documenti", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "appalto",
        sa.Column("categorie_soa", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("appalto", sa.Column("codici_soa", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column("appalto", sa.Column("riassunto", sa.Text(), nullable=True))
    op.add_column("appalto", sa.Column("riassunto_il", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appalto", sa.Column("riassunto_modello", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_appalto_cig"), "appalto", ["cig"], unique=False)
    op.create_check_constraint(
        "ck_appalto_codici_soa", "appalto", "codici_soa IS NULL OR cardinality(codici_soa) > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_appalto_codici_soa", "appalto", type_="check")
    op.drop_index(op.f("ix_appalto_cig"), table_name="appalto")
    op.drop_column("appalto", "riassunto_modello")
    op.drop_column("appalto", "riassunto_il")
    op.drop_column("appalto", "riassunto")
    op.drop_column("appalto", "codici_soa")
    op.drop_column("appalto", "categorie_soa")
    op.drop_column("appalto", "documenti")
    op.drop_column("appalto", "link_partecipazione")
    op.drop_column("appalto", "ente_sito")
    op.drop_column("appalto", "ente_telefono")
    op.drop_column("appalto", "ente_email")
    op.drop_column("appalto", "ente_indirizzo")
    op.drop_column("appalto", "luogo_esecuzione")
    op.drop_column("appalto", "riferimenti_legge")
    op.drop_column("appalto", "descrizione")
    op.drop_column("appalto", "criterio_aggiudicazione")
    op.drop_column("appalto", "tipo_procedura")
    op.drop_column("appalto", "cig")
