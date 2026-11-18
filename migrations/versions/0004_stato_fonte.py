"""Bando: stato dichiarato dalla fonte (portali regionali senza date strutturate).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bando", sa.Column("stato_fonte", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_bando_stato_fonte", "bando", "stato_fonte IN ('aperto', 'in_arrivo', 'chiuso')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_bando_stato_fonte", "bando", type_="check")
    op.drop_column("bando", "stato_fonte")
