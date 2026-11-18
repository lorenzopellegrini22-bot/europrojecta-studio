"""CRM essenziale: aziende clienti, sedi e referenti.

È il sottoinsieme dello schema F1 che serve alle pratiche. Le colonne mancanti (ATECO, dati
dimensionali, de minimis, consensi, ISTAT) arrivano con la fase CRM completa, con nuove
migrazioni che aggiungono colonne senza cambiare queste.
"""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text
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

    sedi: Mapped[list["Sede"]] = relationship(
        back_populates="azienda", cascade="all, delete-orphan", order_by="Sede.id"
    )
    referenti: Mapped[list["Referente"]] = relationship(
        back_populates="azienda",
        cascade="all, delete-orphan",
        order_by="(Referente.principale.desc(), Referente.cognome)",
    )

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

    azienda: Mapped[Azienda] = relationship(back_populates="referenti")

    @property
    def nome_completo(self) -> str:
        return f"{self.nome} {self.cognome or ''}".strip()
