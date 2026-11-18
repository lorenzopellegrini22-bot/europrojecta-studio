"""Integrazioni attivabili e Registro Imprese; ATECO e dati dimensionali dell'azienda;
segnalatori; allegati; prefattibilità; registro delle email inviate.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segnalatore",
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("organizzazione", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("telefono", sa.String(length=40), nullable=True),
        sa.Column("provvigione_percentuale", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("attivo", sa.Boolean(), server_default="true", nullable=False),
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
        sa.CheckConstraint(
            "provvigione_percentuale IS NULL OR "
            "(provvigione_percentuale >= 0 AND provvigione_percentuale <= 100)",
            name="ck_segnalatore_provvigione",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "azienda_ateco",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("codice", sa.String(length=10), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
        sa.Column("versione", sa.String(length=4), nullable=False),
        sa.Column("primario", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint("versione IN ('2007', '2025')", name="ck_azienda_ateco_versione"),
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("azienda_id", "codice", "versione", name="uq_azienda_ateco"),
    )
    op.create_index(
        op.f("ix_azienda_ateco_azienda_id"), "azienda_ateco", ["azienda_id"], unique=False
    )
    op.create_index(
        "uq_azienda_ateco_primario",
        "azienda_ateco",
        ["azienda_id"],
        unique=True,
        postgresql_where=sa.text("primario"),
    )
    op.create_table(
        "notifica_email",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("destinatario", sa.String(length=254), nullable=False),
        sa.Column("oggetto", sa.Text(), nullable=False),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column("pratica_id", sa.BigInteger(), nullable=True),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column("esito", sa.String(length=10), nullable=False),
        sa.Column("errore", sa.Text(), nullable=True),
        sa.Column(
            "inviata_il",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("esito IN ('ok', 'errore')", name="ck_notifica_email_esito"),
        sa.CheckConstraint(
            "tipo IN ('comunicazione_pratica', 'promemoria', 'prova')",
            name="ck_notifica_email_tipo",
        ),
        sa.ForeignKeyConstraint(["pratica_id"], ["pratica.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notifica_email_inviata_il"), "notifica_email", ["inviata_il"], unique=False
    )
    op.create_index(
        op.f("ix_notifica_email_pratica_id"), "notifica_email", ["pratica_id"], unique=False
    )
    op.create_table(
        "prefattibilita",
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("bando_id", sa.BigInteger(), nullable=True),
        sa.Column("bando_testo", sa.Text(), nullable=True),
        sa.Column("esito", sa.String(length=30), nullable=False),
        sa.Column("voci", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("importo_progetto", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("contributo_stimato", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column("pratica_id", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint(
            "esito IN ('da_valutare', 'fattibile', 'fattibile_con_riserva', 'non_fattibile')",
            name="ck_prefattibilita_esito",
        ),
        sa.CheckConstraint(
            "bando_id IS NOT NULL OR bando_testo IS NOT NULL", name="ck_prefattibilita_bando"
        ),
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pratica_id"], ["pratica.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prefattibilita_azienda_id"), "prefattibilita", ["azienda_id"], unique=False
    )
    op.create_table(
        "allegato",
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("pratica_id", sa.BigInteger(), nullable=True),
        sa.Column("pratica_documento_id", sa.BigInteger(), nullable=True),
        sa.Column("nome_originale", sa.Text(), nullable=False),
        sa.Column("percorso", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(length=120), nullable=False),
        sa.Column("dimensione", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("caricato_da", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["caricato_da"], ["utente.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["pratica_documento_id"], ["pratica_documento.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["pratica_id"], ["pratica.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("percorso"),
    )
    op.create_index(op.f("ix_allegato_azienda_id"), "allegato", ["azienda_id"], unique=False)
    op.create_index(
        op.f("ix_allegato_pratica_documento_id"), "allegato", ["pratica_documento_id"], unique=False
    )
    op.create_index(op.f("ix_allegato_pratica_id"), "allegato", ["pratica_id"], unique=False)
    op.add_column("azienda", sa.Column("dipendenti", sa.Integer(), nullable=True))
    op.add_column(
        "azienda", sa.Column("fatturato", sa.Numeric(precision=14, scale=2), nullable=True)
    )
    op.add_column("azienda", sa.Column("anno_dati", sa.Integer(), nullable=True))
    op.add_column("azienda", sa.Column("stato_attivita", sa.String(length=40), nullable=True))
    op.add_column(
        "azienda", sa.Column("registro_imprese_il", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("azienda", sa.Column("segnalatore_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_azienda_segnalatore_id"), "azienda", ["segnalatore_id"], unique=False)
    op.create_foreign_key(
        "fk_azienda_segnalatore_id",
        "azienda",
        "segnalatore",
        ["segnalatore_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "integrazione", sa.Column("attiva", sa.Boolean(), server_default="true", nullable=False)
    )

    op.drop_constraint("ck_integrazione_codice", "integrazione", type_="check")
    op.create_check_constraint(
        "ck_integrazione_codice",
        "integrazione",
        "codice IN ('openai', 'smtp', 'whatsapp', 'registro_imprese')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM integrazione WHERE codice = 'registro_imprese'")
    op.drop_constraint("ck_integrazione_codice", "integrazione", type_="check")
    op.create_check_constraint(
        "ck_integrazione_codice", "integrazione", "codice IN ('openai', 'smtp', 'whatsapp')"
    )
    op.drop_column("integrazione", "attiva")
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint("fk_azienda_segnalatore_id", "azienda", type_="foreignkey")
    op.drop_index(op.f("ix_azienda_segnalatore_id"), table_name="azienda")
    op.drop_column("azienda", "segnalatore_id")
    op.drop_column("azienda", "registro_imprese_il")
    op.drop_column("azienda", "stato_attivita")
    op.drop_column("azienda", "anno_dati")
    op.drop_column("azienda", "fatturato")
    op.drop_column("azienda", "dipendenti")
    op.drop_index(op.f("ix_allegato_pratica_id"), table_name="allegato")
    op.drop_index(op.f("ix_allegato_pratica_documento_id"), table_name="allegato")
    op.drop_index(op.f("ix_allegato_azienda_id"), table_name="allegato")
    op.drop_table("allegato")
    op.drop_index(op.f("ix_prefattibilita_azienda_id"), table_name="prefattibilita")
    op.drop_table("prefattibilita")
    op.drop_index(op.f("ix_notifica_email_pratica_id"), table_name="notifica_email")
    op.drop_index(op.f("ix_notifica_email_inviata_il"), table_name="notifica_email")
    op.drop_table("notifica_email")
    op.drop_index(
        "uq_azienda_ateco_primario",
        table_name="azienda_ateco",
        postgresql_where=sa.text("primario"),
    )
    op.drop_index(op.f("ix_azienda_ateco_azienda_id"), table_name="azienda_ateco")
    op.drop_table("azienda_ateco")
    op.drop_table("segnalatore")
