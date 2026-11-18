"""PDF dei bandi (documento_bando).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documento_bando",
        sa.Column("bando_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("titolo", sa.Text(), nullable=True),
        sa.Column("percorso", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("mime", sa.String(length=100), nullable=True),
        sa.Column("dimensione", sa.BigInteger(), nullable=True),
        sa.Column("pagine", sa.Integer(), nullable=True),
        sa.Column("testo_estratto", sa.Text(), nullable=True),
        sa.Column("scaricato_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativi", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bando_id", "url", name="uq_documento_bando_url"),
    )
    op.create_index(
        op.f("ix_documento_bando_bando_id"), "documento_bando", ["bando_id"], unique=False
    )
    op.create_index(op.f("ix_documento_bando_sha256"), "documento_bando", ["sha256"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_documento_bando_sha256"), table_name="documento_bando")
    op.drop_index(op.f("ix_documento_bando_bando_id"), table_name="documento_bando")
    op.drop_table("documento_bando")
