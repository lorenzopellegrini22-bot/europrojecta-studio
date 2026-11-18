"""Bandi: fonti, esecuzioni della raccolta, bandi, versioni e requisiti risolti.

Semantica dei requisiti (bando_requisiti): NULL = requisito non noto o nessun vincolo noto;
una lista = vincolo. Una lista vuota è vietata da un check constraint.
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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp

STATI_BANDO = ("in_arrivo", "aperto", "chiuso", "sconosciuto")
STATI_DICHIARABILI = ("aperto", "in_arrivo", "chiuso")  # stato_fonte
ESITI_RUN = ("in_corso", "ok", "invariato", "fermato_sanita", "errore")
TERRITORI = ("nazionale", "regionale", "provinciale", "comunale", "sconosciuto")
REGIMI = ("de_minimis", "esenzione", "notificato", "altro", "sconosciuto")
MODALITA = ("sportello", "graduatoria", "click_day", "altro", "sconosciuto")

# Colonne lista di bando_requisiti: nessuna può essere una lista vuota.
COLONNE_LISTA_REQUISITI = (
    "regioni",
    "province",
    "comuni",
    "aree_speciali",
    "dimensioni",
    "ateco_ammessi",
    "ateco_esclusi",
    "forme_giuridiche",
    "tipologie_soggetto",
    "settori",
    "categorie_spesa",
    "forma_agevolazione",
    "finalita",
)


def _in(col: str, valori: tuple[str, ...], nome: str) -> CheckConstraint:
    return CheckConstraint(f"{col} IN {valori}", name=nome)


class Fonte(ConTimestamp, Base):
    __tablename__ = "fonte"

    codice: Mapped[str] = mapped_column(String(60), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    attiva: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    fallimenti_consecutivi: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ultimo_successo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Versione del file già elaborata (il server non offre ETag né richieste condizionali)
    ultimo_last_modified: Mapped[str | None] = mapped_column(String(100))
    ultimo_conteggio: Mapped[int | None] = mapped_column(Integer)


class FonteRun(Base):
    __tablename__ = "fonte_run"
    __table_args__ = (_in("esito", ESITI_RUN, "ck_fonte_run_esito"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fonte_id: Mapped[int] = mapped_column(ForeignKey("fonte.id", ondelete="CASCADE"), index=True)
    inizio: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fine: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    esito: Mapped[str] = mapped_column(String(20), default="in_corso", server_default="in_corso")
    record_letti: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    nuovi: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    modificati: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chiusi: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    invariati: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scomparsi: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errori: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    messaggio: Mapped[str | None] = mapped_column(Text)
    dettagli: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    fonte: Mapped[Fonte] = relationship()


class Bando(ConTimestamp, Base):
    __tablename__ = "bando"
    __table_args__ = (
        UniqueConstraint("fonte_id", "id_esterno", name="uq_bando_fonte_id_esterno"),
        _in("stato", STATI_BANDO, "ck_bando_stato"),
        _in("stato_fonte", STATI_DICHIARABILI, "ck_bando_stato_fonte"),
    )

    fonte_id: Mapped[int] = mapped_column(ForeignKey("fonte.id", ondelete="RESTRICT"))
    id_esterno: Mapped[str] = mapped_column(String(100))
    id_rna: Mapped[str | None] = mapped_column(String(100), index=True)
    titolo: Mapped[str] = mapped_column(Text)
    ente: Mapped[str | None] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    link_ufficiale: Mapped[str | None] = mapped_column(Text)
    link_domande: Mapped[str | None] = mapped_column(Text)
    data_apertura: Mapped[date | None] = mapped_column(Date)
    data_chiusura: Mapped[date | None] = mapped_column(Date, index=True)
    note_apertura_chiusura: Mapped[str | None] = mapped_column(Text)
    stato: Mapped[str] = mapped_column(String(20), index=True)
    # Stato dichiarato dalla fonte (portali regionali): vale solo se mancano le date
    stato_fonte: Mapped[str | None] = mapped_column(String(20))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hash_contenuto: Mapped[str] = mapped_column(String(64))
    visto_la_prima_volta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    versioni_assenti: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scomparso_dal_feed_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bando_canonico_id: Mapped[int | None] = mapped_column(
        ForeignKey("bando.id", ondelete="SET NULL")
    )

    fonte: Mapped[Fonte] = relationship()
    requisiti: Mapped["BandoRequisiti | None"] = relationship(
        back_populates="bando", uselist=False, cascade="all, delete-orphan"
    )


class BandoVersione(Base):
    """Il record raw PRECEDENTE, salvato ogni volta che il bando cambia."""

    __tablename__ = "bando_versione"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bando_id: Mapped[int] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"), index=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hash_contenuto: Mapped[str] = mapped_column(String(64))
    rilevata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def _lista() -> Mapped[list[str] | None]:
    return mapped_column(ARRAY(Text))


class BandoRequisiti(Base):
    __tablename__ = "bando_requisiti"
    __table_args__ = (
        _in("territorio", TERRITORI, "ck_bando_requisiti_territorio"),
        CheckConstraint(f"regime IS NULL OR regime IN {REGIMI}", name="ck_bando_requisiti_regime"),
        CheckConstraint(
            f"modalita IS NULL OR modalita IN {MODALITA}", name="ck_bando_requisiti_modalita"
        ),
        CheckConstraint(
            "ateco_versione IS NULL OR ateco_versione IN ('2007', '2025')",
            name="ck_bando_requisiti_ateco_versione",
        ),
        *(
            CheckConstraint(
                f"{c} IS NULL OR cardinality({c}) > 0", name=f"ck_bando_requisiti_{c}_non_vuota"
            )
            for c in COLONNE_LISTA_REQUISITI
        ),
    )

    bando_id: Mapped[int] = mapped_column(
        ForeignKey("bando.id", ondelete="CASCADE"), primary_key=True
    )
    territorio: Mapped[str] = mapped_column(String(20))
    regioni: Mapped[list[str] | None] = _lista()
    province: Mapped[list[str] | None] = _lista()
    comuni: Mapped[list[str] | None] = _lista()
    aree_speciali: Mapped[list[str] | None] = _lista()
    dimensioni: Mapped[list[str] | None] = _lista()
    ateco_ammessi: Mapped[list[str] | None] = _lista()
    ateco_esclusi: Mapped[list[str] | None] = _lista()
    ateco_versione: Mapped[str | None] = mapped_column(String(4))
    forme_giuridiche: Mapped[list[str] | None] = _lista()
    tipologie_soggetto: Mapped[list[str] | None] = _lista()
    settori: Mapped[list[str] | None] = _lista()
    anzianita_min_mesi: Mapped[int | None] = mapped_column(Integer)
    anzianita_max_mesi: Mapped[int | None] = mapped_column(Integer)
    categorie_spesa: Mapped[list[str] | None] = _lista()
    forma_agevolazione: Mapped[list[str] | None] = _lista()
    finalita: Mapped[list[str] | None] = _lista()
    percentuale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    importo_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    importo_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    spesa_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    spesa_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    dotazione: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    regime: Mapped[str | None] = mapped_column(String(20))
    modalita: Mapped[str | None] = mapped_column(String(20))
    sintesi: Mapped[str | None] = mapped_column(Text)
    provenienza: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    da_verificare: Mapped[list[str] | None] = _lista()
    risolto_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bando: Mapped[Bando] = relationship(back_populates="requisiti")
