"""Allegati nel database (ARCHIVIO_ALLEGATI=database) per le piattaforme senza disco.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-19 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "allegato_contenuto",
        sa.Column(
            "allegato_id",
            sa.BigInteger(),
            sa.ForeignKey("allegato.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("dati", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("allegato_contenuto")
