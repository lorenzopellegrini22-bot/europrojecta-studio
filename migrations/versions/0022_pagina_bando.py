"""Data di lettura della pagina ufficiale del bando (ricerca dei PDF).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-19 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bando", sa.Column("pagina_letta_il", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bando", "pagina_letta_il")
