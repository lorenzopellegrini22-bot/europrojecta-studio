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

LISTE_APPALTO = ("nuts", "regioni", "province", "cpv", "divisioni_cpv", "natura", "codici_soa")


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
    # Scheda completa (TED: campi strutturati; ANAC: file CIG e categorie-opera)
    cig: Mapped[str | None] = mapped_column(String(20), index=True)
    tipo_procedura: Mapped[str | None] = mapped_column(Text)
    criterio_aggiudicazione: Mapped[str | None] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    riferimenti_legge: Mapped[str | None] = mapped_column(Text)
    luogo_esecuzione: Mapped[str | None] = mapped_column(Text)
    ente_indirizzo: Mapped[str | None] = mapped_column(Text)
    ente_email: Mapped[str | None] = mapped_column(Text)
    ente_telefono: Mapped[str | None] = mapped_column(Text)
    ente_sito: Mapped[str | None] = mapped_column(Text)
    link_partecipazione: Mapped[str | None] = mapped_column(Text)
    # [{"titolo", "url"}]
    documenti: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Ultima lettura della pagina della gara per la documentazione (app/appalti/documenti.py)
    documenti_letti_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # [{"codice": "OG6", "descrizione", "tipo": "prevalente"|"scorporabile"|None,
    #   "importo", "provenienza": "anac"|"testo"}]
    categorie_soa: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    codici_soa: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Riassunto scritto dall'AI (solo dati pubblici dell'appalto)
    riassunto: Mapped[str | None] = mapped_column(Text)
    riassunto_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    riassunto_modello: Mapped[str | None] = mapped_column(String(100))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    visto_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
