"""«Password dimenticata»: link via email per scegliere una nuova password.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-23 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIMA = "'ok', 'password_errata', 'totp_errato', 'bloccato', 'utente_inesistente'"
DOPO = PRIMA + ", 'recupero_password'"


def upgrade() -> None:
    op.add_column("utente", sa.Column("recupero_hash", sa.String(64), nullable=True))
    op.add_column(
        "utente", sa.Column("recupero_scade_il", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("ck_log_accesso_esito", "log_accesso", type_="check")
    op.create_check_constraint("ck_log_accesso_esito", "log_accesso", f"esito IN ({DOPO})")


def downgrade() -> None:
    op.execute("DELETE FROM log_accesso WHERE esito = 'recupero_password'")
    op.drop_constraint("ck_log_accesso_esito", "log_accesso", type_="check")
    op.create_check_constraint("ck_log_accesso_esito", "log_accesso", f"esito IN ({PRIMA})")
    op.drop_column("utente", "recupero_scade_il")
    op.drop_column("utente", "recupero_hash")
