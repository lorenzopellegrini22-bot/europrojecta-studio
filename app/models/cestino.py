"""Cestino: copia di quello che si elimina, ripristinabile per 30 giorni (app/cestino)."""

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp
from app.models.auth import Utente


class ElementoCestino(ConTimestamp, Base):
    __tablename__ = "cestino"
    __table_args__ = (Index("ix_cestino_creato_il", "creato_il"),)

    tipo: Mapped[str] = mapped_column(String(40))
    descrizione: Mapped[str] = mapped_column(Text)
    tabella: Mapped[str] = mapped_column(String(80))
    oggetto_id: Mapped[int] = mapped_column(BigInteger)
    # pagina da aprire dopo il ripristino (l'oggetto stesso o la scheda a cui apparteneva)
    link: Mapped[str | None] = mapped_column(Text)
    # JSON: righe da reinserire (l'oggetto e tutto ciò che si elimina con lui) e i collegamenti
    # da ricreare nelle righe che restano (colonne messe a NULL dall'eliminazione)
    dati: Mapped[str] = mapped_column(Text)
    eliminato_da_id: Mapped[int | None] = mapped_column(
        ForeignKey("utente.id", ondelete="SET NULL")
    )

    eliminato_da: Mapped[Utente | None] = relationship()
