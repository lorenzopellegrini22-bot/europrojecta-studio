"""Lavoro di squadra: note con @menzioni, bandi e gare seguiti, calendario personale.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-19 11:01:50.491458
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commento",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("oggetto", sa.String(length=20), nullable=False),
        sa.Column("oggetto_id", sa.BigInteger(), nullable=False),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("oggetto IN ('pratica', 'opportunita')", name="ck_commento_oggetto"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_commento_oggetto", "commento", ["oggetto", "oggetto_id"], unique=False)
    op.create_table(
        "menzione",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("commento_id", sa.BigInteger(), nullable=False),
        sa.Column("utente_id", sa.BigInteger(), nullable=False),
        sa.Column("letta_il", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["commento_id"], ["commento.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commento_id", "utente_id", name="uq_menzione"),
    )
    op.create_index(op.f("ix_menzione_utente_id"), "menzione", ["utente_id"], unique=False)
    op.create_table(
        "seguito",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("utente_id", sa.BigInteger(), nullable=False),
        sa.Column("bando_id", sa.BigInteger(), nullable=True),
        sa.Column("appalto_id", sa.BigInteger(), nullable=True),
        sa.Column("istantanea", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "(bando_id IS NOT NULL) <> (appalto_id IS NOT NULL)", name="ck_seguito_oggetto"
        ),
        sa.ForeignKeyConstraint(["appalto_id"], ["appalto.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("utente_id", "appalto_id", name="uq_seguito_appalto"),
        sa.UniqueConstraint("utente_id", "bando_id", name="uq_seguito_bando"),
    )
    op.create_index(op.f("ix_seguito_utente_id"), "seguito", ["utente_id"], unique=False)
    op.create_table(
        "novita_seguito",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("seguito_id", sa.BigInteger(), nullable=False),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column(
            "creata_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("letta_il", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["seguito_id"], ["seguito.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_novita_seguito_seguito_id"), "novita_seguito", ["seguito_id"], unique=False
    )
    op.add_column("utente", sa.Column("calendario_hash", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_utente_calendario_hash", "utente", ["calendario_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_utente_calendario_hash", "utente", type_="unique")
    op.drop_column("utente", "calendario_hash")
    op.drop_index(op.f("ix_novita_seguito_seguito_id"), table_name="novita_seguito")
    op.drop_table("novita_seguito")
    op.drop_index(op.f("ix_seguito_utente_id"), table_name="seguito")
    op.drop_table("seguito")
    op.drop_index(op.f("ix_menzione_utente_id"), table_name="menzione")
    op.drop_table("menzione")
    op.drop_index("ix_commento_oggetto", table_name="commento")
    op.drop_table("commento")
