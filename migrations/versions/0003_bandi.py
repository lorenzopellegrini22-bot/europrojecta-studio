"""Bandi: fonti, esecuzioni della raccolta, bandi, versioni, requisiti; registro dei job.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)

LISTE = (
    "regioni",
    "province",
    "comuni",
    "aree_speciali",
    "dimensioni",
    "ateco_ammessi",
    "ateco_esclusi",
    "forme_giuridiche",
    "tipologie_soggetto",
    "settori",
    "categorie_spesa",
    "forma_agevolazione",
    "finalita",
)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True)


def _timestamp() -> list[sa.Column]:
    return [
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("aggiornato_il", TS, nullable=False, server_default=sa.func.now()),
    ]


def _cnt(nome: str) -> sa.Column:
    return sa.Column(nome, sa.Integer, nullable=False, server_default="0")


def upgrade() -> None:
    op.create_table(
        "fonte",
        _id(),
        sa.Column("codice", sa.String(60), nullable=False, unique=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("attiva", sa.Boolean, nullable=False, server_default="true"),
        _cnt("fallimenti_consecutivi"),
        sa.Column("ultimo_successo", TS),
        sa.Column("ultimo_last_modified", sa.String(100)),
        sa.Column("ultimo_conteggio", sa.Integer),
        *_timestamp(),
    )

    op.create_table(
        "fonte_run",
        _id(),
        sa.Column(
            "fonte_id",
            sa.BigInteger,
            sa.ForeignKey("fonte.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("inizio", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("fine", TS),
        sa.Column("esito", sa.String(20), nullable=False, server_default="in_corso"),
        _cnt("record_letti"),
        _cnt("nuovi"),
        _cnt("modificati"),
        _cnt("chiusi"),
        _cnt("invariati"),
        _cnt("scomparsi"),
        _cnt("errori"),
        sa.Column("messaggio", sa.Text),
        sa.Column("dettagli", JSONB),
        sa.CheckConstraint(
            "esito IN ('in_corso', 'ok', 'invariato', 'fermato_sanita', 'errore')",
            name="ck_fonte_run_esito",
        ),
    )

    op.create_table(
        "bando",
        _id(),
        sa.Column(
            "fonte_id",
            sa.BigInteger,
            sa.ForeignKey("fonte.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("id_esterno", sa.String(100), nullable=False),
        sa.Column("id_rna", sa.String(100), index=True),
        sa.Column("titolo", sa.Text, nullable=False),
        sa.Column("ente", sa.Text),
        sa.Column("descrizione", sa.Text),
        sa.Column("link_ufficiale", sa.Text),
        sa.Column("link_domande", sa.Text),
        sa.Column("data_apertura", sa.Date),
        sa.Column("data_chiusura", sa.Date, index=True),
        sa.Column("note_apertura_chiusura", sa.Text),
        sa.Column("stato", sa.String(20), nullable=False, index=True),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("hash_contenuto", sa.String(64), nullable=False),
        sa.Column("visto_la_prima_volta", TS, nullable=False, server_default=sa.func.now()),
        _cnt("versioni_assenti"),
        sa.Column("scomparso_dal_feed_il", TS),
        sa.Column(
            "bando_canonico_id", sa.BigInteger, sa.ForeignKey("bando.id", ondelete="SET NULL")
        ),
        *_timestamp(),
        sa.UniqueConstraint("fonte_id", "id_esterno", name="uq_bando_fonte_id_esterno"),
        sa.CheckConstraint(
            "stato IN ('in_arrivo', 'aperto', 'chiuso', 'sconosciuto')", name="ck_bando_stato"
        ),
    )

    op.create_table(
        "bando_versione",
        _id(),
        sa.Column(
            "bando_id",
            sa.BigInteger,
            sa.ForeignKey("bando.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("hash_contenuto", sa.String(64), nullable=False),
        sa.Column("rilevata_il", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "bando_requisiti",
        sa.Column(
            "bando_id",
            sa.BigInteger,
            sa.ForeignKey("bando.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("territorio", sa.String(20), nullable=False),
        *[sa.Column(c, ARRAY(sa.Text)) for c in LISTE[:7]],
        sa.Column("ateco_versione", sa.String(4)),
        *[sa.Column(c, ARRAY(sa.Text)) for c in LISTE[7:10]],
        sa.Column("anzianita_min_mesi", sa.Integer),
        sa.Column("anzianita_max_mesi", sa.Integer),
        *[sa.Column(c, ARRAY(sa.Text)) for c in LISTE[10:]],
        sa.Column("percentuale", sa.Numeric(5, 2)),
        sa.Column("importo_min", sa.Numeric(14, 2)),
        sa.Column("importo_max", sa.Numeric(14, 2)),
        sa.Column("spesa_min", sa.Numeric(14, 2)),
        sa.Column("spesa_max", sa.Numeric(14, 2)),
        sa.Column("dotazione", sa.Numeric(16, 2)),
        sa.Column("regime", sa.String(20)),
        sa.Column("modalita", sa.String(20)),
        sa.Column("sintesi", sa.Text),
        sa.Column("provenienza", JSONB, nullable=False, server_default="{}"),
        sa.Column("da_verificare", ARRAY(sa.Text)),
        sa.Column("risolto_il", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "territorio IN ('nazionale', 'regionale', 'provinciale', 'comunale', 'sconosciuto')",
            name="ck_bando_requisiti_territorio",
        ),
        sa.CheckConstraint(
            "regime IS NULL OR regime IN "
            "('de_minimis', 'esenzione', 'notificato', 'altro', 'sconosciuto')",
            name="ck_bando_requisiti_regime",
        ),
        sa.CheckConstraint(
            "modalita IS NULL OR modalita IN "
            "('sportello', 'graduatoria', 'click_day', 'altro', 'sconosciuto')",
            name="ck_bando_requisiti_modalita",
        ),
        sa.CheckConstraint(
            "ateco_versione IS NULL OR ateco_versione IN ('2007', '2025')",
            name="ck_bando_requisiti_ateco_versione",
        ),
        *[
            sa.CheckConstraint(
                f"{c} IS NULL OR cardinality({c}) > 0", name=f"ck_bando_requisiti_{c}_non_vuota"
            )
            for c in LISTE
        ],
    )

    # Registro di ogni esecuzione dei job del worker (raccolta, pulizia, …)
    op.create_table(
        "job_run",
        _id(),
        sa.Column("job", sa.String(60), nullable=False, index=True),
        sa.Column("inizio", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("fine", TS),
        sa.Column("esito", sa.String(20), nullable=False, server_default="in_corso"),
        sa.Column("messaggio", sa.Text),
        sa.CheckConstraint(
            "esito IN ('in_corso', 'ok', 'errore', 'saltato')", name="ck_job_run_esito"
        ),
    )


def downgrade() -> None:
    for t in ("job_run", "bando_requisiti", "bando_versione", "bando", "fonte_run", "fonte"):
        op.drop_table(t)
