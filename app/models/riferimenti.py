"""Tabelle di riferimento ISTAT (brief, sezione 5): comuni e raccordo ATECO 2007/2025.

Si caricano dai file ufficiali ISTAT con `python -m app.cli carica-istat` (e ogni mese dal
worker); vedi app/riferimenti/istat.py.
"""

from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Comune(Base):
    """Elenco dei comuni italiani (ISTAT, «Codici delle unità amministrative»)."""

    __tablename__ = "comune"
    __table_args__ = (Index("ix_comune_nome_norm", "nome_norm"),)

    codice_istat: Mapped[str] = mapped_column(String(6), primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    # Nome minuscolo, senza accenti e apostrofi: per confrontare i nomi scritti in modi diversi
    nome_norm: Mapped[str] = mapped_column(Text)
    sigla_provincia: Mapped[str] = mapped_column(String(2), index=True)
    provincia: Mapped[str] = mapped_column(Text)  # unità territoriale sovracomunale
    regione: Mapped[str] = mapped_column(String(40))
    codice_catastale: Mapped[str | None] = mapped_column(String(4))


class AtecoRaccordo(Base):
    """Tavola di raccordo ISTAT tra ATECO 2025 e ATECO 2007 (aggiornamento 2022), a tutti i
    livelli (sezione, divisione, gruppo, classe, categoria, sottocategoria)."""

    __tablename__ = "ateco_raccordo"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codice_2025: Mapped[str] = mapped_column(String(10), index=True)
    codice_2007: Mapped[str] = mapped_column(String(10), index=True)
    copertura_2025: Mapped[str | None] = mapped_column(String(10))  # TOTALE / PARZIALE
    copertura_2007: Mapped[str | None] = mapped_column(String(10))
