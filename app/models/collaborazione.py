"""Lavoro di squadra: note con @menzioni su pratiche e opportunità, bandi e gare seguiti."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.auth import Utente

OGGETTI_COMMENTO = ("pratica", "opportunita")


class Commento(Base):
    """Nota interna dello studio su una pratica o un'opportunità (mai mostrata ai clienti)."""

    __tablename__ = "commento"
    __table_args__ = (
        CheckConstraint(f"oggetto IN {OGGETTI_COMMENTO}", name="ck_commento_oggetto"),
        Index("ix_commento_oggetto", "oggetto", "oggetto_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    oggetto: Mapped[str] = mapped_column(String(20))
    oggetto_id: Mapped[int] = mapped_column(BigInteger)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    testo: Mapped[str] = mapped_column(Text)
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    utente: Mapped[Utente | None] = relationship()
    menzioni: Mapped[list["Menzione"]] = relationship(
        back_populates="commento", cascade="all, delete-orphan"
    )


class Menzione(Base):
    """Collega citato con @nome in una nota: la vede in «Oggi» finché non la segna letta."""

    __tablename__ = "menzione"
    __table_args__ = (UniqueConstraint("commento_id", "utente_id", name="uq_menzione"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    commento_id: Mapped[int] = mapped_column(ForeignKey("commento.id", ondelete="CASCADE"))
    utente_id: Mapped[int] = mapped_column(ForeignKey("utente.id", ondelete="CASCADE"), index=True)
    letta_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    commento: Mapped[Commento] = relationship(back_populates="menzioni")
    utente: Mapped[Utente] = relationship()


class Seguito(Base):
    """Bando o gara seguiti da un utente. `istantanea` sono i dati controllati ogni mattina
    (scadenza, stato, documenti): se cambiano nasce una novità."""

    __tablename__ = "seguito"
    __table_args__ = (
        CheckConstraint(
            "(bando_id IS NOT NULL) <> (appalto_id IS NOT NULL)", name="ck_seguito_oggetto"
        ),
        UniqueConstraint("utente_id", "bando_id", name="uq_seguito_bando"),
        UniqueConstraint("utente_id", "appalto_id", name="uq_seguito_appalto"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    utente_id: Mapped[int] = mapped_column(ForeignKey("utente.id", ondelete="CASCADE"), index=True)
    bando_id: Mapped[int | None] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"))
    appalto_id: Mapped[int | None] = mapped_column(ForeignKey("appalto.id", ondelete="CASCADE"))
    istantanea: Mapped[dict[str, Any]] = mapped_column(JSONB)
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    utente: Mapped[Utente] = relationship()
    novita: Mapped[list["NovitaSeguito"]] = relationship(
        back_populates="seguito", cascade="all, delete-orphan"
    )


class NovitaSeguito(Base):
    __tablename__ = "novita_seguito"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seguito_id: Mapped[int] = mapped_column(
        ForeignKey("seguito.id", ondelete="CASCADE"), index=True
    )
    testo: Mapped[str] = mapped_column(Text)
    creata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    letta_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    seguito: Mapped[Seguito] = relationship(back_populates="novita")
