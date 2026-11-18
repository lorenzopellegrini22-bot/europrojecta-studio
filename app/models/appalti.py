"""Appalti pubblici (bandi di gara) raccolti dalla piattaforma europea TED.

Una riga per avviso (publication-number). Le liste (NUTS, regioni, province, CPV, natura)
servono ai filtri: NULL = non indicato, mai lista vuota.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, ConTimestamp

LISTE_APPALTO = ("nuts", "regioni", "province", "cpv", "divisioni_cpv", "natura")


class Appalto(ConTimestamp, Base):
    __tablename__ = "appalto"
    __table_args__ = tuple(
        CheckConstraint(f"{c} IS NULL OR cardinality({c}) > 0", name=f"ck_appalto_{c}")
        for c in LISTE_APPALTO
    )

    fonte: Mapped[str] = mapped_column(String(30), default="ted")
    id_esterno: Mapped[str] = mapped_column(String(40), unique=True)
    titolo: Mapped[str] = mapped_column(Text)
    stazione_appaltante: Mapped[str | None] = mapped_column(Text)
    citta: Mapped[str | None] = mapped_column(Text)
    nuts: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    regioni: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    province: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    cpv: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    divisioni_cpv: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    natura: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    scadenza: Mapped[date | None] = mapped_column(Date, index=True)
    pubblicazione: Mapped[date | None] = mapped_column(Date, index=True)
    importo_stimato: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    link: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    visto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
