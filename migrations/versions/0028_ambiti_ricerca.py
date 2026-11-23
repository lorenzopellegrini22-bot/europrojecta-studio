"""Ambiti di ricerca per cliente (bandi UE, nazionali, regionali e locali, gare d'appalto).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-23 23:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "azienda", sa.Column("ambiti_ricerca", postgresql.ARRAY(sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("azienda", "ambiti_ricerca")
