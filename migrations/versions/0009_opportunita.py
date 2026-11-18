"""Opportunità per cliente (bandi e appalti) e profilo del cliente per l'abbinamento.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunita",
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("bando_id", sa.BigInteger(), nullable=True),
        sa.Column("appalto_id", sa.BigInteger(), nullable=True),
        sa.Column("punteggio", sa.Integer(), nullable=False),
        sa.Column("motivi", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stato", sa.String(length=20), nullable=False),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("trovata_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notificata_il", sa.DateTime(timezone=True), nullable=True),
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
            "stato IN ('nuova', 'vista', 'interessante', 'scartata', 'in_pratica')",
            name="ck_opportunita_stato",
        ),
        sa.CheckConstraint(
            "(bando_id IS NOT NULL)::int + (appalto_id IS NOT NULL)::int = 1",
            name="ck_opportunita_uno_solo",
        ),
        sa.ForeignKeyConstraint(["appalto_id"], ["appalto.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_opportunita_azienda_id"), "opportunita", ["azienda_id"], unique=False)
    op.create_index(op.f("ix_opportunita_stato"), "opportunita", ["stato"], unique=False)
    op.create_index(
        "uq_opportunita_appalto",
        "opportunita",
        ["azienda_id", "appalto_id"],
        unique=True,
        postgresql_where=sa.text("appalto_id IS NOT NULL"),
    )
    op.create_index(
        "uq_opportunita_bando",
        "opportunita",
        ["azienda_id", "bando_id"],
        unique=True,
        postgresql_where=sa.text("bando_id IS NOT NULL"),
    )
    op.add_column(
        "azienda", sa.Column("avvisi_attivi", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column(
        "azienda",
        sa.Column("soa_possedute", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "azienda", sa.Column("regioni_interesse", postgresql.ARRAY(sa.Text()), nullable=True)
    )
    op.add_column("azienda", sa.Column("cpv_interesse", postgresql.ARRAY(sa.Text()), nullable=True))
    op.create_check_constraint(
        "ck_azienda_regioni_interesse",
        "azienda",
        "regioni_interesse IS NULL OR cardinality(regioni_interesse) > 0",
    )
    op.create_check_constraint(
        "ck_azienda_cpv_interesse",
        "azienda",
        "cpv_interesse IS NULL OR cardinality(cpv_interesse) > 0",
    )
    op.drop_constraint("ck_notifica_email_tipo", "notifica_email", type_="check")
    op.create_check_constraint(
        "ck_notifica_email_tipo",
        "notifica_email",
        "tipo IN ('comunicazione_pratica', 'promemoria', 'prova', 'opportunita')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifica_email WHERE tipo = 'opportunita'")
    op.drop_constraint("ck_notifica_email_tipo", "notifica_email", type_="check")
    op.create_check_constraint(
        "ck_notifica_email_tipo",
        "notifica_email",
        "tipo IN ('comunicazione_pratica', 'promemoria', 'prova')",
    )
    op.drop_constraint("ck_azienda_cpv_interesse", "azienda", type_="check")
    op.drop_constraint("ck_azienda_regioni_interesse", "azienda", type_="check")
    op.drop_column("azienda", "cpv_interesse")
    op.drop_column("azienda", "regioni_interesse")
    op.drop_column("azienda", "soa_possedute")
    op.drop_column("azienda", "avvisi_attivi")
    op.drop_index(
        "uq_opportunita_bando",
        table_name="opportunita",
        postgresql_where=sa.text("bando_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_opportunita_appalto",
        table_name="opportunita",
        postgresql_where=sa.text("appalto_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_opportunita_stato"), table_name="opportunita")
    op.drop_index(op.f("ix_opportunita_azienda_id"), table_name="opportunita")
    op.drop_table("opportunita")
