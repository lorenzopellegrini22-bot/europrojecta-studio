"""PDF dei bandi scaricati da link diretti (schema F2: documento_bando)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, ConTimestamp


class DocumentoBando(ConTimestamp, Base):
    __tablename__ = "documento_bando"
    __table_args__ = (UniqueConstraint("bando_id", "url", name="uq_documento_bando_url"),)

    bando_id: Mapped[int] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    titolo: Mapped[str | None] = mapped_column(Text)
    percorso: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    mime: Mapped[str | None] = mapped_column(String(100))
    dimensione: Mapped[int | None] = mapped_column(BigInteger)
    pagine: Mapped[int | None] = mapped_column(Integer)
    testo_estratto: Mapped[str | None] = mapped_column(Text)
    scaricato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativi: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errore: Mapped[str | None] = mapped_column(Text)
