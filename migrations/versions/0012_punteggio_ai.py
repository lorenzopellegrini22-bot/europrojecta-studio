"""Fase 4: embeddings, valutazione AI e feedback delle opportunità; impostazioni.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("entita", sa.String(length=20), nullable=False),
        sa.Column("entita_id", sa.BigInteger(), nullable=False),
        sa.Column("modello", sa.String(length=100), nullable=False),
        sa.Column("hash_testo", sa.String(length=64), nullable=False),
        sa.Column("vettore", Vector(), nullable=False),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("entita IN ('bando', 'appalto', 'azienda')", name="ck_embedding_entita"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entita", "entita_id", "modello", name="uq_embedding_entita_modello"),
    )
    op.add_column(
        "impostazioni_ai",
        sa.Column("valutazioni_per_notte", sa.Integer(), server_default="100", nullable=False),
    )
    op.add_column(
        "impostazioni_ai",
        sa.Column("valutazioni_per_cliente", sa.Integer(), server_default="10", nullable=False),
    )
    op.add_column(
        "impostazioni_ai",
        sa.Column("soglia_similarita", sa.Float(), server_default="0.2", nullable=False),
    )
    op.add_column(
        "impostazioni_ai",
        sa.Column("soglia_notifica", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column("opportunita", sa.Column("punteggio_regole", sa.Integer(), nullable=True))
    op.add_column("opportunita", sa.Column("similarita", sa.Float(), nullable=True))
    op.add_column("opportunita", sa.Column("punteggio_ai", sa.Integer(), nullable=True))
    op.add_column("opportunita", sa.Column("motivazione_ai", sa.Text(), nullable=True))
    op.add_column(
        "opportunita",
        sa.Column("requisiti_ai", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("opportunita", sa.Column("modello_ai", sa.String(length=100), nullable=True))
    op.add_column("opportunita", sa.Column("hash_input_ai", sa.String(length=64), nullable=True))
    op.add_column(
        "opportunita", sa.Column("valutata_il", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("opportunita", sa.Column("feedback", sa.String(length=20), nullable=True))
    op.add_column(
        "opportunita", sa.Column("feedback_il", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("opportunita", sa.Column("feedback_utente_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_opportunita_feedback_utente_id",
        "opportunita",
        "utente",
        ["feedback_utente_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("UPDATE opportunita SET punteggio_regole = punteggio")
    op.create_check_constraint(
        "ck_opportunita_feedback",
        "opportunita",
        "feedback IS NULL OR feedback IN ('utile', 'non_pertinente', 'gia_noto')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_opportunita_feedback", "opportunita", type_="check")
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint("fk_opportunita_feedback_utente_id", "opportunita", type_="foreignkey")
    op.drop_column("opportunita", "feedback_utente_id")
    op.drop_column("opportunita", "feedback_il")
    op.drop_column("opportunita", "feedback")
    op.drop_column("opportunita", "valutata_il")
    op.drop_column("opportunita", "hash_input_ai")
    op.drop_column("opportunita", "modello_ai")
    op.drop_column("opportunita", "requisiti_ai")
    op.drop_column("opportunita", "motivazione_ai")
    op.drop_column("opportunita", "punteggio_ai")
    op.drop_column("opportunita", "similarita")
    op.drop_column("opportunita", "punteggio_regole")
    op.drop_column("impostazioni_ai", "soglia_notifica")
    op.drop_column("impostazioni_ai", "soglia_similarita")
    op.drop_column("impostazioni_ai", "valutazioni_per_cliente")
    op.drop_column("impostazioni_ai", "valutazioni_per_notte")
    op.drop_table("embedding")
