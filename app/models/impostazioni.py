"""Integrazioni esterne (OpenAI, SMTP, WhatsApp): configurazione e segreti cifrati."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CODICI_INTEGRAZIONE = ("openai", "smtp", "whatsapp", "registro_imprese")


class Integrazione(Base):
    """Una riga per integrazione.

    - `config`: i campi non segreti, in chiaro (es. host SMTP).
    - `segreti`: campo -> valore cifrato con Fernet (vedi app/sicurezza/cifratura.py).
    - `anteprime`: campo -> ultime 4 cifre, l'unica parte di un segreto mostrata in pagina.
    """

    __tablename__ = "integrazione"
    __table_args__ = (
        CheckConstraint(f"codice IN {CODICI_INTEGRAZIONE}", name="ck_integrazione_codice"),
    )

    codice: Mapped[str] = mapped_column(String(30), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    segreti: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    anteprime: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Interruttore: un'integrazione disattivata non viene usata dall'app (le chiavi restano)
    attiva: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    verificata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifica_ok: Mapped[bool | None] = mapped_column(Boolean)
    verifica_messaggio: Mapped[str | None] = mapped_column(Text)
    aggiornato_da: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
