"""Fase 3: impostazioni AI, estrazioni dei bandi, correzioni manuali, consumi; valori della
fonte nei requisiti.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "impostazioni_ai",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("modello_estrazione", sa.String(length=100), nullable=True),
        sa.Column("modello_scoring", sa.String(length=100), nullable=True),
        sa.Column("modello_embeddings", sa.String(length=100), nullable=True),
        sa.Column("budget_mensile_eur", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "prezzi", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("estrazioni_per_notte", sa.Integer(), server_default="50", nullable=False),
        sa.Column("sospesa_fino_al", sa.Date(), nullable=True),
        sa.Column("motivo_sospensione", sa.Text(), nullable=True),
        sa.Column("avviso_80_inviato_mese", sa.String(length=7), nullable=True),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_impostazioni_ai_riga_unica"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "uso_llm",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("operazione", sa.String(length=20), nullable=False),
        sa.Column("modello", sa.String(length=100), nullable=False),
        sa.Column("token_ingresso", sa.Integer(), nullable=False),
        sa.Column("token_uscita", sa.Integer(), nullable=False),
        sa.Column("costo_stimato", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("esito", sa.String(length=10), nullable=False),
        sa.Column("errore", sa.Text(), nullable=True),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("esito IN ('ok', 'errore')", name="ck_uso_llm_esito"),
        sa.CheckConstraint(
            "operazione IN ('estrazione', 'scoring', 'embedding', 'aggiornamento', 'riassunto')",
            name="ck_uso_llm_operazione",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_uso_llm_creato_il"), "uso_llm", ["creato_il"], unique=False)
    op.create_table(
        "bando_override",
        sa.Column("bando_id", sa.BigInteger(), nullable=False),
        sa.Column("campo", sa.String(length=40), nullable=False),
        sa.Column("valore", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bando_id", "campo", name="uq_bando_override_campo"),
    )
    op.create_index(
        op.f("ix_bando_override_bando_id"), "bando_override", ["bando_id"], unique=False
    )
    op.create_table(
        "estrazione_bando",
        sa.Column("bando_id", sa.BigInteger(), nullable=False),
        sa.Column("modello", sa.String(length=100), nullable=False),
        sa.Column("versione_prompt", sa.String(length=20), nullable=False),
        sa.Column("versione_schema", sa.String(length=20), nullable=False),
        sa.Column("hash_input", sa.String(length=64), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stato_revisione", sa.String(length=20), nullable=False),
        sa.Column("token_ingresso", sa.Integer(), nullable=True),
        sa.Column("token_uscita", sa.Integer(), nullable=True),
        sa.Column("costo_stimato", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("errore", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
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
            "stato_revisione IN ('da_revisionare', 'revisionato')",
            name="ck_estrazione_bando_stato_revisione",
        ),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_estrazione_bando_bando_id"), "estrazione_bando", ["bando_id"], unique=False
    )
    op.create_index(
        op.f("ix_estrazione_bando_hash_input"), "estrazione_bando", ["hash_input"], unique=False
    )
    op.add_column(
        "bando_requisiti",
        sa.Column("valori_fonte", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bando_requisiti", "valori_fonte")
    op.drop_index(op.f("ix_estrazione_bando_hash_input"), table_name="estrazione_bando")
    op.drop_index(op.f("ix_estrazione_bando_bando_id"), table_name="estrazione_bando")
    op.drop_table("estrazione_bando")
    op.drop_index(op.f("ix_bando_override_bando_id"), table_name="bando_override")
    op.drop_table("bando_override")
    op.drop_index(op.f("ix_uso_llm_creato_il"), table_name="uso_llm")
    op.drop_table("uso_llm")
    op.drop_table("impostazioni_ai")
