"""Testo della pagina ufficiale del bando, letto per l'AI quando i PDF non bastano.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-21 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bando", sa.Column("testo_pagina", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bando", "testo_pagina")
