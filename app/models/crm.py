"""CRM essenziale: aziende clienti, sedi e referenti.

È il sottoinsieme dello schema F1 che serve alle pratiche. Le colonne mancanti (ATECO, dati
dimensionali, de minimis, consensi, ISTAT) arrivano con la fase CRM completa, con nuove
migrazioni che aggiungono colonne senza cambiare queste.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp

STATI_CLIENTE = ("prospect", "attivo", "ex")
TIPI_SEDE = ("legale", "operativa")
DIMENSIONI = ("micro", "piccola", "media", "grande")


class Azienda(ConTimestamp, Base):
    __tablename__ = "azienda"
    __table_args__ = (
        CheckConstraint(f"stato_cliente IN {STATI_CLIENTE}", name="ck_azienda_stato_cliente"),
        CheckConstraint(
            f"dimensione_manuale IS NULL OR dimensione_manuale IN {DIMENSIONI}",
            name="ck_azienda_dimensione_manuale",
        ),
        CheckConstraint(
            "partita_iva IS NULL OR partita_iva ~ '^[0-9]{11}$'", name="ck_azienda_partita_iva"
        ),
        CheckConstraint(
            "regioni_interesse IS NULL OR cardinality(regioni_interesse) > 0",
            name="ck_azienda_regioni_interesse",
        ),
        CheckConstraint(
            "cpv_interesse IS NULL OR cardinality(cpv_interesse) > 0",
            name="ck_azienda_cpv_interesse",
        ),
    )

    ragione_sociale: Mapped[str] = mapped_column(Text)
    partita_iva: Mapped[str | None] = mapped_column(String(11), unique=True)
    codice_fiscale: Mapped[str | None] = mapped_column(String(16))
    forma_giuridica: Mapped[str | None] = mapped_column(String(100))
    dimensione_manuale: Mapped[str | None] = mapped_column(String(10))
    pec: Mapped[str | None] = mapped_column(String(254))
    email: Mapped[str | None] = mapped_column(String(254))
    telefono: Mapped[str | None] = mapped_column(String(40))
    codice_sdi: Mapped[str | None] = mapped_column(String(7))
    stato_cliente: Mapped[str] = mapped_column(String(20), default="attivo")
    note: Mapped[str | None] = mapped_column(Text)
    # Dati dimensionali (schema F1); dal Registro Imprese quando collegato
    dipendenti: Mapped[int | None] = mapped_column(Integer)
    fatturato: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    totale_attivo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    data_costituzione: Mapped[date | None] = mapped_column(Date)
    # Se indicato prevale sul plafond calcolato dagli aiuti registrati
    plafond_de_minimis_manuale: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    frequenza_aggiornamenti: Mapped[str] = mapped_column(
        String(20), default="nessuno", server_default="nessuno"
    )
    id_crm_origine: Mapped[str | None] = mapped_column(String(100))
    anno_dati: Mapped[int | None] = mapped_column(Integer)
    # Stato dell'attività al Registro Imprese (es. ATTIVA, CESSATA) e data dell'ultima lettura
    stato_attivita: Mapped[str | None] = mapped_column(String(40))
    registro_imprese_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segnalatore_id: Mapped[int | None] = mapped_column(
        ForeignKey("segnalatore.id", ondelete="SET NULL"), index=True
    )
    # Profilo per le opportunità (abbinamento automatico con bandi e appalti)
    avvisi_attivi: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Attestazione SOA: [{"codice": "OG6", "classifica": "III"}]
    soa_possedute: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Regioni in cui il cliente partecipa agli appalti (oltre a quella della sede)
    regioni_interesse: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Divisioni CPV (2 cifre) di interesse per servizi e forniture
    cpv_interesse: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    sedi: Mapped[list["Sede"]] = relationship(
        back_populates="azienda", cascade="all, delete-orphan", order_by="Sede.id"
    )
    referenti: Mapped[list["Referente"]] = relationship(
        back_populates="azienda",
        cascade="all, delete-orphan",
        order_by="(Referente.principale.desc(), Referente.cognome)",
    )

    ateco: Mapped[list["AziendaAteco"]] = relationship(
        back_populates="azienda",
        cascade="all, delete-orphan",
        order_by="(AziendaAteco.primario.desc(), AziendaAteco.codice)",
    )
    segnalatore: Mapped["Segnalatore | None"] = relationship(back_populates="aziende")
    tag: Mapped[list["Tag"]] = relationship(secondary="azienda_tag", order_by="Tag.nome")  # noqa: F821

    @property
    def ateco_primario(self) -> "AziendaAteco | None":
        return next((a for a in self.ateco if a.primario), None)

    @property
    def sede_legale(self) -> "Sede | None":
        return next((s for s in self.sedi if s.tipo == "legale"), None)

    @property
    def referente_principale(self) -> "Referente | None":
        return next((r for r in self.referenti if r.principale), None) or (
            self.referenti[0] if self.referenti else None
        )


class Sede(ConTimestamp, Base):
    __tablename__ = "sede"
    __table_args__ = (CheckConstraint(f"tipo IN {TIPI_SEDE}", name="ck_sede_tipo"),)

    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(20))
    indirizzo: Mapped[str | None] = mapped_column(Text)
    cap: Mapped[str | None] = mapped_column(String(5))
    comune_testo: Mapped[str | None] = mapped_column(Text)
    provincia: Mapped[str | None] = mapped_column(String(2))
    regione: Mapped[str | None] = mapped_column(String(40))
    codice_istat: Mapped[str | None] = mapped_column(String(6))

    azienda: Mapped[Azienda] = relationship(back_populates="sedi")


class Referente(ConTimestamp, Base):
    __tablename__ = "referente"

    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(Text)
    cognome: Mapped[str | None] = mapped_column(Text)
    ruolo: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(254))
    telefono: Mapped[str | None] = mapped_column(String(40))
    principale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)
    # Consenso agli aggiornamenti periodici (brief, sezione 5)
    consenso_email: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    consenso_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    consenso_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canale_preferito: Mapped[str | None] = mapped_column(String(10))
    disiscritto_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    azienda: Mapped[Azienda] = relationship(back_populates="referenti")

    @property
    def nome_completo(self) -> str:
        return f"{self.nome} {self.cognome or ''}".strip()


class AziendaAteco(Base):
    """Codici ATECO dell'azienda (schema F1): al massimo un primario."""

    __tablename__ = "azienda_ateco"
    __table_args__ = (
        UniqueConstraint("azienda_id", "codice", "versione", name="uq_azienda_ateco"),
        CheckConstraint("versione IN ('2007', '2025')", name="ck_azienda_ateco_versione"),
        Index(
            "uq_azienda_ateco_primario",
            "azienda_id",
            unique=True,
            postgresql_where=text("primario"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    codice: Mapped[str] = mapped_column(String(10))
    descrizione: Mapped[str | None] = mapped_column(Text)
    versione: Mapped[str] = mapped_column(String(4))
    primario: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    azienda: Mapped[Azienda] = relationship(back_populates="ateco")


class Segnalatore(ConTimestamp, Base):
    """Chi porta clienti allo studio, con l'eventuale provvigione sul contributo concesso."""

    __tablename__ = "segnalatore"
    __table_args__ = (
        CheckConstraint(
            "provvigione_percentuale IS NULL OR "
            "(provvigione_percentuale >= 0 AND provvigione_percentuale <= 100)",
            name="ck_segnalatore_provvigione",
        ),
    )

    nome: Mapped[str] = mapped_column(Text)
    organizzazione: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(254))
    telefono: Mapped[str | None] = mapped_column(String(40))
    provvigione_percentuale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    note: Mapped[str | None] = mapped_column(Text)
    attivo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    aziende: Mapped[list[Azienda]] = relationship(back_populates="segnalatore")
