"""Sezione Automazioni: regole che preparano o inviano messaggi ai clienti.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-26 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.automazioni.servizio import PREDEFINITE

    op.create_table(
        "automazione",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("attiva", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("attiva_dal", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evento", sa.String(length=20), nullable=False),
        sa.Column("giorni_scadenza", sa.Integer(), nullable=True),
        sa.Column("frequenza", sa.String(length=20), nullable=True),
        sa.Column("destinatari", sa.String(length=10), nullable=False),
        sa.Column("aziende_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("ambiti", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("punteggio_minimo", sa.Integer(), nullable=False),
        sa.Column("azione", sa.String(length=10), nullable=False),
        sa.Column("canale", sa.String(length=10), nullable=False),
        sa.Column("oggetto", sa.Text(), nullable=False),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column("usa_ai", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column("ultima_esecuzione", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_esito", sa.Text(), nullable=True),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evento IN ('nuovo_bando', 'scadenza', 'riepilogo')", name="ck_automazione_evento"
        ),
        sa.CheckConstraint(
            "destinatari IN ('tutti', 'imprese', 'enti', 'scelti')",
            name="ck_automazione_destinatari",
        ),
        sa.CheckConstraint("azione IN ('bozza', 'invio')", name="ck_automazione_azione"),
        sa.CheckConstraint(
            "canale IN ('preferito', 'email', 'whatsapp')", name="ck_automazione_canale"
        ),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "aggiornamento_cliente", sa.Column("automazione_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "aggiornamento_cliente",
        sa.Column("appalti_citati", postgresql.ARRAY(sa.BigInteger()), nullable=True),
    )
    op.create_index(
        op.f("ix_aggiornamento_cliente_automazione_id"),
        "aggiornamento_cliente",
        ["automazione_id"],
    )
    op.create_foreign_key(
        "aggiornamento_cliente_automazione_id_fkey",
        "aggiornamento_cliente",
        "automazione",
        ["automazione_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # le due automazioni pronte, spente: le accende il titolare
    tabella = sa.table(
        "automazione",
        sa.column("nome", sa.String),
        sa.column("evento", sa.String),
        sa.column("giorni_scadenza", sa.Integer),
        sa.column("destinatari", sa.String),
        sa.column("punteggio_minimo", sa.Integer),
        sa.column("azione", sa.String),
        sa.column("canale", sa.String),
        sa.column("oggetto", sa.Text),
        sa.column("testo", sa.Text),
    )
    op.bulk_insert(
        tabella,
        [
            {
                "nome": p["nome"],
                "evento": p["evento"],
                "giorni_scadenza": p.get("giorni_scadenza"),
                "destinatari": "tutti",
                "punteggio_minimo": p["punteggio_minimo"],
                "azione": "bozza",
                "canale": "preferito",
                "oggetto": p["oggetto"],
                "testo": p["testo"],
            }
            for p in PREDEFINITE
        ],
    )


def downgrade() -> None:
    op.drop_constraint(
        "aggiornamento_cliente_automazione_id_fkey", "aggiornamento_cliente", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_aggiornamento_cliente_automazione_id"), table_name="aggiornamento_cliente"
    )
    op.drop_column("aggiornamento_cliente", "appalti_citati")
    op.drop_column("aggiornamento_cliente", "automazione_id")
    op.drop_table("automazione")
