"""Utenti: ruolo «lettura» e sezioni assegnate dal titolare.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("utente", sa.Column("sezioni", postgresql.ARRAY(sa.Text()), nullable=True))
    op.drop_constraint("ck_utente_ruolo", "utente", type_="check")
    op.create_check_constraint(
        "ck_utente_ruolo", "utente", "ruolo IN ('titolare', 'collaboratore', 'lettura')"
    )


def downgrade() -> None:
    op.execute("UPDATE utente SET ruolo = 'collaboratore' WHERE ruolo = 'lettura'")
    op.drop_constraint("ck_utente_ruolo", "utente", type_="check")
    op.create_check_constraint(
        "ck_utente_ruolo", "utente", "ruolo IN ('titolare', 'collaboratore')"
    )
    op.drop_column("utente", "sezioni")
