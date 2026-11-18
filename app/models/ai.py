"""Intelligenza artificiale (fase 3): impostazioni, estrazioni dei bandi, correzioni manuali,
registro dei consumi.

- `impostazioni_ai`: una sola riga (id = 1) con modelli, budget mensile e prezzi per milione
  di token; la API key resta nell'integrazione OpenAI (cifrata).
- `estrazione_bando`: ogni estrazione (anche fallita), con modello, versioni di prompt e
  schema, hash dell'input e token; l'ultima riuscita alimenta i requisiti.
- `bando_override`: correzione manuale di un campo (prevale su fonte e AI).
- `uso_llm`: ogni chiamata, con il costo stimato.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp
from app.models.auth import Utente

OPERAZIONI_LLM = ("estrazione", "scoring", "embedding", "aggiornamento", "riassunto")


class ImpostazioniAI(Base):
    __tablename__ = "impostazioni_ai"
    __table_args__ = (CheckConstraint("id = 1", name="ck_impostazioni_ai_riga_unica"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    modello_estrazione: Mapped[str | None] = mapped_column(String(100))
    modello_scoring: Mapped[str | None] = mapped_column(String(100))
    modello_embeddings: Mapped[str | None] = mapped_column(String(100))
    budget_mensile_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # modello → {"ingresso": €/milione token, "uscita": €/milione token}
    prezzi: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    estrazioni_per_notte: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    sospesa_fino_al: Mapped[date | None] = mapped_column(Date)
    motivo_sospensione: Mapped[str | None] = mapped_column(Text)
    avviso_80_inviato_mese: Mapped[str | None] = mapped_column(String(7))  # "2026-11"
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EstrazioneBando(ConTimestamp, Base):
    __tablename__ = "estrazione_bando"
    __table_args__ = (
        CheckConstraint(
            "stato_revisione IN ('da_revisionare', 'revisionato')",
            name="ck_estrazione_bando_stato_revisione",
        ),
    )

    bando_id: Mapped[int] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"), index=True)
    modello: Mapped[str] = mapped_column(String(100))
    versione_prompt: Mapped[str] = mapped_column(String(20))
    versione_schema: Mapped[str] = mapped_column(String(20))
    hash_input: Mapped[str] = mapped_column(String(64), index=True)
    # Campi verificati: nome → {valore, confidenza, citazione, da_verificare}
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stato_revisione: Mapped[str] = mapped_column(String(20), default="da_revisionare")
    token_ingresso: Mapped[int | None] = mapped_column(Integer)
    token_uscita: Mapped[int | None] = mapped_column(Integer)
    costo_stimato: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    errore: Mapped[str | None] = mapped_column(Text)


class BandoOverride(ConTimestamp, Base):
    __tablename__ = "bando_override"
    __table_args__ = (UniqueConstraint("bando_id", "campo", name="uq_bando_override_campo"),)

    bando_id: Mapped[int] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"), index=True)
    campo: Mapped[str] = mapped_column(String(40))
    valore: Mapped[Any] = mapped_column(JSONB)
    nota: Mapped[str | None] = mapped_column(Text)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))

    utente: Mapped[Utente | None] = relationship()


class UsoLLM(Base):
    __tablename__ = "uso_llm"
    __table_args__ = (
        CheckConstraint(f"operazione IN {OPERAZIONI_LLM}", name="ck_uso_llm_operazione"),
        CheckConstraint("esito IN ('ok', 'errore')", name="ck_uso_llm_esito"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operazione: Mapped[str] = mapped_column(String(20))
    modello: Mapped[str] = mapped_column(String(100))
    token_ingresso: Mapped[int] = mapped_column(Integer, default=0)
    token_uscita: Mapped[int] = mapped_column(Integer, default=0)
    costo_stimato: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    esito: Mapped[str] = mapped_column(String(10))
    errore: Mapped[str | None] = mapped_column(Text)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
