"""Clienti (azienda, sede, referente) e pratiche a fasi con modelli di pratica.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "azienda",
        sa.Column("ragione_sociale", sa.Text(), nullable=False),
        sa.Column("partita_iva", sa.String(length=11), nullable=True),
        sa.Column("codice_fiscale", sa.String(length=16), nullable=True),
        sa.Column("forma_giuridica", sa.String(length=100), nullable=True),
        sa.Column("dimensione_manuale", sa.String(length=10), nullable=True),
        sa.Column("pec", sa.String(length=254), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("telefono", sa.String(length=40), nullable=True),
        sa.Column("codice_sdi", sa.String(length=7), nullable=True),
        sa.Column("stato_cliente", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
            "dimensione_manuale IS NULL OR "
            "dimensione_manuale IN ('micro', 'piccola', 'media', 'grande')",
            name="ck_azienda_dimensione_manuale",
        ),
        sa.CheckConstraint(
            "partita_iva IS NULL OR partita_iva ~ '^[0-9]{11}$'", name="ck_azienda_partita_iva"
        ),
        sa.CheckConstraint(
            "stato_cliente IN ('prospect', 'attivo', 'ex')", name="ck_azienda_stato_cliente"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("partita_iva"),
    )
    op.create_table(
        "modello_pratica",
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nome"),
    )
    op.create_table(
        "modello_fase",
        sa.Column("modello_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["modello_id"], ["modello_pratica.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_modello_fase_modello_id"), "modello_fase", ["modello_id"], unique=False
    )
    op.create_table(
        "referente",
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("cognome", sa.Text(), nullable=True),
        sa.Column("ruolo", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("telefono", sa.String(length=40), nullable=True),
        sa.Column("principale", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_referente_azienda_id"), "referente", ["azienda_id"], unique=False)
    op.create_table(
        "sede",
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("indirizzo", sa.Text(), nullable=True),
        sa.Column("cap", sa.String(length=5), nullable=True),
        sa.Column("comune_testo", sa.Text(), nullable=True),
        sa.Column("provincia", sa.String(length=2), nullable=True),
        sa.Column("regione", sa.String(length=40), nullable=True),
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
        sa.CheckConstraint("tipo IN ('legale', 'operativa')", name="ck_sede_tipo"),
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sede_azienda_id"), "sede", ["azienda_id"], unique=False)
    op.create_table(
        "modello_operazione",
        sa.Column("fase_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("titolo", sa.Text(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("giorni_previsti", sa.Integer(), nullable=True),
        sa.Column("documenti", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("campi", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("oggetto_email", sa.Text(), nullable=True),
        sa.Column("testo_email", sa.Text(), nullable=True),
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
            "tipo IN ('attivita', 'documenti', 'dati', 'comunicazione')",
            name="ck_modello_operazione_tipo",
        ),
        sa.CheckConstraint("campi IS NULL OR cardinality(campi) > 0", name="ck_modello_op_campi"),
        sa.CheckConstraint(
            "documenti IS NULL OR cardinality(documenti) > 0", name="ck_modello_op_documenti"
        ),
        sa.CheckConstraint(
            "giorni_previsti IS NULL OR giorni_previsti >= 0", name="ck_modello_op_giorni"
        ),
        sa.ForeignKeyConstraint(["fase_id"], ["modello_fase.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_modello_operazione_fase_id"), "modello_operazione", ["fase_id"], unique=False
    )
    op.create_table(
        "pratica",
        sa.Column("codice", sa.String(length=20), nullable=False),
        sa.Column("azienda_id", sa.BigInteger(), nullable=False),
        sa.Column("bando_id", sa.BigInteger(), nullable=True),
        sa.Column("bando_testo", sa.Text(), nullable=True),
        sa.Column("modello_id", sa.BigInteger(), nullable=True),
        sa.Column("stato", sa.String(length=20), nullable=False),
        sa.Column("responsabile_id", sa.BigInteger(), nullable=True),
        sa.Column("importo_richiesto", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("importo_concesso", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("protocollo", sa.Text(), nullable=True),
        sa.Column("scadenza_presentazione", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            "stato IN ('individuato', 'proposto', 'in_preparazione', 'presentata', "
            "'ammessa', 'respinta', 'rendicontazione', 'chiusa')",
            name="ck_pratica_stato",
        ),
        sa.CheckConstraint(
            "bando_id IS NOT NULL OR bando_testo IS NOT NULL", name="ck_pratica_bando"
        ),
        sa.ForeignKeyConstraint(["azienda_id"], ["azienda.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bando_id"], ["bando.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["modello_id"], ["modello_pratica.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responsabile_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("codice"),
    )
    op.create_index(op.f("ix_pratica_azienda_id"), "pratica", ["azienda_id"], unique=False)
    op.create_index(op.f("ix_pratica_bando_id"), "pratica", ["bando_id"], unique=False)
    op.create_index(
        op.f("ix_pratica_responsabile_id"), "pratica", ["responsabile_id"], unique=False
    )
    op.create_index(
        op.f("ix_pratica_scadenza_presentazione"),
        "pratica",
        ["scadenza_presentazione"],
        unique=False,
    )
    op.create_table(
        "pratica_evento",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("pratica_id", sa.BigInteger(), nullable=False),
        sa.Column("da_stato", sa.String(length=20), nullable=True),
        sa.Column("a_stato", sa.String(length=20), nullable=True),
        sa.Column("nota", sa.Text(), nullable=False),
        sa.Column("utente_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "creato_il", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["pratica_id"], ["pratica.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utente_id"], ["utente.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pratica_evento_pratica_id"), "pratica_evento", ["pratica_id"], unique=False
    )
    op.create_table(
        "pratica_fase",
        sa.Column("pratica_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
        sa.Column("stato", sa.String(length=20), nullable=False),
        sa.Column("iniziata_il", sa.Date(), nullable=True),
        sa.Column("completata_il", sa.Date(), nullable=True),
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
            "stato IN ('da_iniziare', 'in_corso', 'completata', 'saltata')",
            name="ck_pratica_fase_stato",
        ),
        sa.ForeignKeyConstraint(["pratica_id"], ["pratica.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pratica_fase_pratica_id"), "pratica_fase", ["pratica_id"], unique=False
    )
    op.create_table(
        "pratica_operazione",
        sa.Column("fase_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("titolo", sa.Text(), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("stato", sa.String(length=20), nullable=False),
        sa.Column("giorni_previsti", sa.Integer(), nullable=True),
        sa.Column("scadenza", sa.Date(), nullable=True),
        sa.Column("assegnata_a", sa.BigInteger(), nullable=True),
        sa.Column("completata_il", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completata_da", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("oggetto_email", sa.Text(), nullable=True),
        sa.Column("testo_email", sa.Text(), nullable=True),
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
            "stato IN ('da_fare', 'fatta', 'non_applicabile')", name="ck_pratica_operazione_stato"
        ),
        sa.CheckConstraint(
            "tipo IN ('attivita', 'documenti', 'dati', 'comunicazione')",
            name="ck_pratica_operazione_tipo",
        ),
        sa.ForeignKeyConstraint(["assegnata_a"], ["utente.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completata_da"], ["utente.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fase_id"], ["pratica_fase.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pratica_operazione_fase_id"), "pratica_operazione", ["fase_id"], unique=False
    )
    op.create_index(
        op.f("ix_pratica_operazione_scadenza"), "pratica_operazione", ["scadenza"], unique=False
    )
    op.create_table(
        "pratica_dato",
        sa.Column("operazione_id", sa.BigInteger(), nullable=False),
        sa.Column("etichetta", sa.Text(), nullable=False),
        sa.Column("valore", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["operazione_id"], ["pratica_operazione.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pratica_dato_operazione_id"), "pratica_dato", ["operazione_id"], unique=False
    )
    op.create_table(
        "pratica_documento",
        sa.Column("operazione_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("stato", sa.String(length=20), nullable=False),
        sa.Column("nota", sa.Text(), nullable=True),
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
            "stato IN ('mancante', 'richiesto', 'ricevuto', 'verificato')",
            name="ck_pratica_documento_stato",
        ),
        sa.ForeignKeyConstraint(["operazione_id"], ["pratica_operazione.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pratica_documento_operazione_id"),
        "pratica_documento",
        ["operazione_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pratica_documento_operazione_id"), table_name="pratica_documento")
    op.drop_table("pratica_documento")
    op.drop_index(op.f("ix_pratica_dato_operazione_id"), table_name="pratica_dato")
    op.drop_table("pratica_dato")
    op.drop_index(op.f("ix_pratica_operazione_scadenza"), table_name="pratica_operazione")
    op.drop_index(op.f("ix_pratica_operazione_fase_id"), table_name="pratica_operazione")
    op.drop_table("pratica_operazione")
    op.drop_index(op.f("ix_pratica_fase_pratica_id"), table_name="pratica_fase")
    op.drop_table("pratica_fase")
    op.drop_index(op.f("ix_pratica_evento_pratica_id"), table_name="pratica_evento")
    op.drop_table("pratica_evento")
    op.drop_index(op.f("ix_pratica_scadenza_presentazione"), table_name="pratica")
    op.drop_index(op.f("ix_pratica_responsabile_id"), table_name="pratica")
    op.drop_index(op.f("ix_pratica_bando_id"), table_name="pratica")
    op.drop_index(op.f("ix_pratica_azienda_id"), table_name="pratica")
    op.drop_table("pratica")
    op.drop_index(op.f("ix_modello_operazione_fase_id"), table_name="modello_operazione")
    op.drop_table("modello_operazione")
    op.drop_index(op.f("ix_sede_azienda_id"), table_name="sede")
    op.drop_table("sede")
    op.drop_index(op.f("ix_referente_azienda_id"), table_name="referente")
    op.drop_table("referente")
    op.drop_index(op.f("ix_modello_fase_modello_id"), table_name="modello_fase")
    op.drop_table("modello_fase")
    op.drop_table("modello_pratica")
    op.drop_table("azienda")
