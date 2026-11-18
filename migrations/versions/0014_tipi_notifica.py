"""Nuovi tipi di email registrate: aggiornamenti ai clienti, riepilogo del mattino, avvisi.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUOVI = (
    "comunicazione_pratica",
    "promemoria",
    "prova",
    "opportunita",
    "aggiornamento_cliente",
    "riepilogo_mattutino",
    "avviso",
)
VECCHI = NUOVI[:4]


def _vincolo(tipi: tuple[str, ...]) -> None:
    op.drop_constraint("ck_notifica_email_tipo", "notifica_email", type_="check")
    op.create_check_constraint("ck_notifica_email_tipo", "notifica_email", f"tipo IN {tipi}")


def upgrade() -> None:
    _vincolo(NUOVI)


def downgrade() -> None:
    op.execute(f"DELETE FROM notifica_email WHERE tipo NOT IN {VECCHI}")
    _vincolo(VECCHI)
