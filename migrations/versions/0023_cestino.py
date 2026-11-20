"""Cestino: quello che si elimina resta ripristinabile per 30 giorni.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-20 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cestino",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=False),
        sa.Column("tabella", sa.String(80), nullable=False),
        sa.Column("oggetto_id", sa.BigInteger(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("dati", sa.Text(), nullable=False),
        sa.Column("eliminato_da_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["eliminato_da_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cestino_creato_il", "cestino", ["creato_il"])


def downgrade() -> None:
    op.drop_index("ix_cestino_creato_il", table_name="cestino")
    op.drop_table("cestino")
