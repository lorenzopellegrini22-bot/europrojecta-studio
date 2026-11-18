"""Accesso (utenti, 2FA, sessioni, log, audit) e integrazioni con chiavi API cifrate.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "utente",
        _id(),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("totp_secret_cifrato", sa.Text),
        sa.Column("totp_attivo", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("totp_ultimo_intervallo", sa.BigInteger),
        sa.Column("ruolo", sa.String(20), nullable=False),
        sa.Column("attivo", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tentativi_falliti", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bloccato_fino_al", TS),
        sa.Column("ultimo_accesso", TS),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("aggiornato_il", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ruolo IN ('titolare', 'collaboratore')", name="ck_utente_ruolo"),
        sa.CheckConstraint("email = lower(email)", name="ck_utente_email_minuscola"),
    )

    op.create_table(
        "codice_recupero",
        _id(),
        sa.Column(
            "utente_id",
            sa.BigInteger,
            sa.ForeignKey("utente.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("codice_hash", sa.String(64), nullable=False),
        sa.Column("usato_il", TS),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "sessione",
        _id(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column(
            "utente_id",
            sa.BigInteger,
            sa.ForeignKey("utente.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("mfa_verificata", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("scade_il", TS, nullable=False),
        sa.Column("ultimo_uso", TS, nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.Text),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "log_accesso",
        _id(),
        sa.Column("email_tentata", sa.String(254), nullable=False),
        sa.Column("utente_id", sa.BigInteger, sa.ForeignKey("utente.id", ondelete="SET NULL")),
        sa.Column("esito", sa.String(30), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.Text),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now(), index=True),
        sa.CheckConstraint(
            "esito IN ('ok', 'password_errata', 'totp_errato', 'bloccato', 'utente_inesistente')",
            name="ck_log_accesso_esito",
        ),
    )

    op.create_table(
        "audit_log",
        _id(),
        sa.Column("utente_id", sa.BigInteger, sa.ForeignKey("utente.id", ondelete="SET NULL")),
        sa.Column("entita", sa.String(60), nullable=False),
        sa.Column("entita_id", sa.String(60)),
        sa.Column("azione", sa.String(20), nullable=False),
        sa.Column("differenze", JSONB),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now(), index=True),
        sa.CheckConstraint(
            "azione IN ('crea', 'modifica', 'elimina', 'esporta', 'invia')",
            name="ck_audit_log_azione",
        ),
    )

    op.create_table(
        "integrazione",
        sa.Column("codice", sa.String(30), primary_key=True),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("segreti", JSONB, nullable=False, server_default="{}"),
        sa.Column("anteprime", JSONB, nullable=False, server_default="{}"),
        sa.Column("verificata_il", TS),
        sa.Column("verifica_ok", sa.Boolean),
        sa.Column("verifica_messaggio", sa.Text),
        sa.Column("aggiornato_da", sa.BigInteger, sa.ForeignKey("utente.id", ondelete="SET NULL")),
        sa.Column("creato_il", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("aggiornato_il", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "codice IN ('openai', 'smtp', 'whatsapp')", name="ck_integrazione_codice"
        ),
    )


def downgrade() -> None:
    for tabella in (
        "integrazione",
        "audit_log",
        "log_accesso",
        "sessione",
        "codice_recupero",
        "utente",
    ):
        op.drop_table(tabella)
