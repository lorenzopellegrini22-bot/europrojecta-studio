"""Uso dell'AI: nuova operazione «ocr» (PDF scansionati trascritti dall'AI dove manca Tesseract).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-19 00:40:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIMA = ("estrazione", "scoring", "embedding", "aggiornamento", "riassunto")
DOPO = (*PRIMA, "ocr")


def upgrade() -> None:
    op.drop_constraint("ck_uso_llm_operazione", "uso_llm", type_="check")
    op.create_check_constraint("ck_uso_llm_operazione", "uso_llm", f"operazione IN {DOPO}")


def downgrade() -> None:
    op.execute("DELETE FROM uso_llm WHERE operazione = 'ocr'")
    op.drop_constraint("ck_uso_llm_operazione", "uso_llm", type_="check")
    op.create_check_constraint("ck_uso_llm_operazione", "uso_llm", f"operazione IN {PRIMA}")
