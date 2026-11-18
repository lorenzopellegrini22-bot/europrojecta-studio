"""Opportunità: un bando o un appalto adatto a un cliente, trovato dall'abbinamento notturno.

Nello schema iniziale c'era la tabella `match` (solo bandi, con campi per l'LLM della fase 4).
Qui serve anche per gli appalti, quindi una tabella unica con un riferimento all'uno o
all'altro; i campi LLM si aggiungeranno quando arriverà il punteggio con l'AI.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp
from app.models.appalti import Appalto
from app.models.bandi import Bando
from app.models.crm import Azienda

STATI_OPPORTUNITA = ("nuova", "vista", "interessante", "scartata", "in_pratica")
FEEDBACK = ("utile", "non_pertinente", "gia_noto")


class Opportunita(ConTimestamp, Base):
    __tablename__ = "opportunita"
    __table_args__ = (
        CheckConstraint(f"stato IN {STATI_OPPORTUNITA}", name="ck_opportunita_stato"),
        CheckConstraint(
            f"feedback IS NULL OR feedback IN {FEEDBACK}", name="ck_opportunita_feedback"
        ),
        CheckConstraint(
            "(bando_id IS NOT NULL)::int + (appalto_id IS NOT NULL)::int = 1",
            name="ck_opportunita_uno_solo",
        ),
        Index(
            "uq_opportunita_bando",
            "azienda_id",
            "bando_id",
            unique=True,
            postgresql_where=text("bando_id IS NOT NULL"),
        ),
        Index(
            "uq_opportunita_appalto",
            "azienda_id",
            "appalto_id",
            unique=True,
            postgresql_where=text("appalto_id IS NOT NULL"),
        ),
    )

    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    bando_id: Mapped[int | None] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"))
    appalto_id: Mapped[int | None] = mapped_column(ForeignKey("appalto.id", ondelete="CASCADE"))
    # Progetto dell'azienda più adatto al bando (similarità più alta)
    progetto_id: Mapped[int | None] = mapped_column(ForeignKey("progetto.id", ondelete="SET NULL"))
    # Punteggio mostrato: quello dell'AI se c'è, altrimenti quello delle regole
    punteggio: Mapped[int] = mapped_column(Integer)
    punteggio_regole: Mapped[int | None] = mapped_column(Integer)
    # Valutazione AI (fase 4): solo dati anonimi del cliente
    similarita: Mapped[float | None] = mapped_column(Float)
    punteggio_ai: Mapped[int | None] = mapped_column(Integer)
    motivazione_ai: Mapped[str | None] = mapped_column(Text)
    # {"soddisfatti": [...], "da_verificare": [...], "mancanti": [...]}
    requisiti_ai: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    modello_ai: Mapped[str | None] = mapped_column(String(100))
    hash_input_ai: Mapped[str | None] = mapped_column(String(64))
    valutata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[str | None] = mapped_column(String(20))
    feedback_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_utente_id: Mapped[int | None] = mapped_column(
        ForeignKey("utente.id", ondelete="SET NULL")
    )
    # {"soddisfatti": [...], "da_verificare": [...]}
    motivi: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stato: Mapped[str] = mapped_column(String(20), default="nuova", index=True)
    nota: Mapped[str | None] = mapped_column(Text)
    trovata_il: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notificata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    azienda: Mapped[Azienda] = relationship()
    bando: Mapped[Bando | None] = relationship()
    appalto: Mapped[Appalto | None] = relationship()

    @property
    def titolo(self) -> str:
        return self.bando.titolo if self.bando else (self.appalto.titolo if self.appalto else "")

    @property
    def scadenza(self):  # type: ignore[no-untyped-def]
        if self.bando:
            return self.bando.data_chiusura
        return self.appalto.scadenza if self.appalto else None
