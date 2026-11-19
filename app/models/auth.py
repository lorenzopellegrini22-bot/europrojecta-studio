"""Accesso e sicurezza: utenti, codici di recupero, sessioni, log degli accessi, audit."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp

RUOLI = ("titolare", "collaboratore", "lettura")
ESITI_ACCESSO = ("ok", "password_errata", "totp_errato", "bloccato", "utente_inesistente")
AZIONI_AUDIT = ("crea", "modifica", "elimina", "esporta", "invia")


class Utente(ConTimestamp, Base):
    __tablename__ = "utente"
    __table_args__ = (
        CheckConstraint(f"ruolo IN {RUOLI}", name="ck_utente_ruolo"),
        CheckConstraint("email = lower(email)", name="ck_utente_email_minuscola"),
        UniqueConstraint("calendario_hash", name="uq_utente_calendario_hash"),
    )

    email: Mapped[str] = mapped_column(String(254), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret_cifrato: Mapped[str | None] = mapped_column(Text)
    totp_attivo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Ultimo intervallo TOTP usato: impedisce di riusare lo stesso codice due volte.
    totp_ultimo_intervallo: Mapped[int | None] = mapped_column(BigInteger)
    ruolo: Mapped[str] = mapped_column(String(20))
    # Sezioni dell'app assegnate (app/auth/permessi.py); None = predefinite del ruolo
    sezioni: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Primo accesso: con la password temporanea va cambiata prima di usare l'app
    deve_cambiare_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Invito via email: impronta del token del link (mai il token in chiaro) e scadenza
    invito_hash: Mapped[str | None] = mapped_column(String(64))
    invito_scade_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Impronta del token del link al calendario personale (.ics); None = link non creato
    calendario_hash: Mapped[str | None] = mapped_column(String(64))
    attivo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    tentativi_falliti: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bloccato_fino_al: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_accesso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    codici_recupero: Mapped[list["CodiceRecupero"]] = relationship(
        back_populates="utente", cascade="all, delete-orphan"
    )

    @property
    def is_titolare(self) -> bool:
        return self.ruolo == "titolare"


class CodiceRecupero(Base):
    __tablename__ = "codice_recupero"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    utente_id: Mapped[int] = mapped_column(ForeignKey("utente.id", ondelete="CASCADE"), index=True)
    codice_hash: Mapped[str] = mapped_column(String(64))
    usato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    utente: Mapped[Utente] = relationship(back_populates="codici_recupero")


class Sessione(Base):
    __tablename__ = "sessione"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    utente_id: Mapped[int] = mapped_column(ForeignKey("utente.id", ondelete="CASCADE"), index=True)
    mfa_verificata: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultimo_uso: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    utente: Mapped[Utente] = relationship(lazy="joined")


class LogAccesso(Base):
    __tablename__ = "log_accesso"
    __table_args__ = (CheckConstraint(f"esito IN {ESITI_ACCESSO}", name="ck_log_accesso_esito"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email_tentata: Mapped[str] = mapped_column(String(254))
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    esito: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (CheckConstraint(f"azione IN {AZIONI_AUDIT}", name="ck_audit_log_azione"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    entita: Mapped[str] = mapped_column(String(60))
    entita_id: Mapped[str | None] = mapped_column(String(60))
    azione: Mapped[str] = mapped_column(String(20))
    differenze: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
