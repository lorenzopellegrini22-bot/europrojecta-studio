"""Clienti enti pubblici (comuni, province, unioni…): tipo di cliente e abitanti.

I clienti già presenti il cui nome inizia con «Comune di», «Provincia di», «Unione dei
comuni»… diventano enti pubblici (stessa regola di app/utils/enti.py, tipo_dal_nome).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-23 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "azienda",
        sa.Column("tipo_soggetto", sa.String(20), nullable=False, server_default="impresa"),
    )
    op.add_column("azienda", sa.Column("abitanti", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_azienda_tipo_soggetto", "azienda", "tipo_soggetto IN ('impresa', 'ente_pubblico')"
    )
    from app.utils.enti import tipo_dal_nome

    conn = op.get_bind()
    for id_, nome, forma in conn.execute(
        sa.text("SELECT id, ragione_sociale, forma_giuridica FROM azienda")
    ):
        if tipo_dal_nome(nome, forma) == "ente_pubblico":
            conn.execute(
                sa.text("UPDATE azienda SET tipo_soggetto = 'ente_pubblico' WHERE id = :id"),
                {"id": id_},
            )


def downgrade() -> None:
    op.drop_constraint("ck_azienda_tipo_soggetto", "azienda", type_="check")
    op.drop_column("azienda", "abitanti")
    op.drop_column("azienda", "tipo_soggetto")
