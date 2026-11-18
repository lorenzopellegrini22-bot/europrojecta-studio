"""Appalti pubblici raccolti da TED.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appalto",
        sa.Column("fonte", sa.String(length=30), nullable=False),
        sa.Column("id_esterno", sa.String(length=40), nullable=False),
        sa.Column("titolo", sa.Text(), nullable=False),
        sa.Column("stazione_appaltante", sa.Text(), nullable=True),
        sa.Column("citta", sa.Text(), nullable=True),
        sa.Column("nuts", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("regioni", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("province", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("cpv", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("divisioni_cpv", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("natura", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("scadenza", sa.Date(), nullable=True),
        sa.Column("pubblicazione", sa.Date(), nullable=True),
        sa.Column("importo_stimato", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "visto_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "aggiornato_il",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("cpv IS NULL OR cardinality(cpv) > 0", name="ck_appalto_cpv"),
        sa.CheckConstraint(
            "divisioni_cpv IS NULL OR cardinality(divisioni_cpv) > 0",
            name="ck_appalto_divisioni_cpv",
        ),
        sa.CheckConstraint("natura IS NULL OR cardinality(natura) > 0", name="ck_appalto_natura"),
        sa.CheckConstraint("nuts IS NULL OR cardinality(nuts) > 0", name="ck_appalto_nuts"),
        sa.CheckConstraint(
            "province IS NULL OR cardinality(province) > 0", name="ck_appalto_province"
        ),
        sa.CheckConstraint(
            "regioni IS NULL OR cardinality(regioni) > 0", name="ck_appalto_regioni"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_esterno"),
    )
    op.create_index(op.f("ix_appalto_pubblicazione"), "appalto", ["pubblicazione"], unique=False)
    op.create_index(op.f("ix_appalto_scadenza"), "appalto", ["scadenza"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_appalto_scadenza"), table_name="appalto")
    op.drop_index(op.f("ix_appalto_pubblicazione"), table_name="appalto")
    op.drop_table("appalto")
