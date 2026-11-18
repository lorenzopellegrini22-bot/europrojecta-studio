"""Pratiche a fasi e modelli di pratica.

- Un MODELLO (modello_pratica) descrive un percorso tipo: fasi numerate, ognuna con
  operazioni (attività, checklist documenti, dati da raccogliere, comunicazione al cliente).
- Una PRATICA nasce da un modello: fasi e operazioni vengono COPIATE nella pratica, così si
  possono personalizzare senza toccare il modello e le modifiche al modello non cambiano le
  pratiche già aperte.
- Lo stato della pratica (individuato … chiusa, come da schema) è separato dalla fase in corso.
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
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp
from app.models.auth import Utente
from app.models.bandi import Bando
from app.models.crm import Azienda

STATI_PRATICA = (
    "individuato",
    "proposto",
    "in_preparazione",
    "presentata",
    "ammessa",
    "respinta",
    "rendicontazione",
    "chiusa",
)
STATI_PRATICA_CONCLUSI = ("respinta", "chiusa")
TIPI_OPERAZIONE = ("attivita", "documenti", "dati", "comunicazione")
STATI_FASE = ("da_iniziare", "in_corso", "completata", "saltata")
STATI_OPERAZIONE = ("da_fare", "fatta", "non_applicabile")
STATI_DOCUMENTO = ("mancante", "richiesto", "ricevuto", "verificato")


def _in(col: str, valori: tuple[str, ...], nome: str) -> CheckConstraint:
    return CheckConstraint(f"{col} IN {valori}", name=nome)


def _lista_testi() -> Mapped[list[str] | None]:
    return mapped_column(ARRAY(Text))


# --- Modelli -----------------------------------------------------------------------------


class ModelloPratica(ConTimestamp, Base):
    __tablename__ = "modello_pratica"

    nome: Mapped[str] = mapped_column(Text, unique=True)
    descrizione: Mapped[str | None] = mapped_column(Text)
    attivo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    fasi: Mapped[list["ModelloFase"]] = relationship(
        back_populates="modello", cascade="all, delete-orphan", order_by="ModelloFase.numero"
    )


class ModelloFase(ConTimestamp, Base):
    __tablename__ = "modello_fase"

    modello_id: Mapped[int] = mapped_column(
        ForeignKey("modello_pratica.id", ondelete="CASCADE"), index=True
    )
    numero: Mapped[int] = mapped_column(Integer)
    nome: Mapped[str] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)

    modello: Mapped[ModelloPratica] = relationship(back_populates="fasi")
    operazioni: Mapped[list["ModelloOperazione"]] = relationship(
        back_populates="fase", cascade="all, delete-orphan", order_by="ModelloOperazione.numero"
    )


class ModelloOperazione(ConTimestamp, Base):
    __tablename__ = "modello_operazione"
    __table_args__ = (
        _in("tipo", TIPI_OPERAZIONE, "ck_modello_operazione_tipo"),
        CheckConstraint(
            "documenti IS NULL OR cardinality(documenti) > 0", name="ck_modello_op_documenti"
        ),
        CheckConstraint("campi IS NULL OR cardinality(campi) > 0", name="ck_modello_op_campi"),
        CheckConstraint(
            "giorni_previsti IS NULL OR giorni_previsti >= 0", name="ck_modello_op_giorni"
        ),
    )

    fase_id: Mapped[int] = mapped_column(
        ForeignKey("modello_fase.id", ondelete="CASCADE"), index=True
    )
    numero: Mapped[int] = mapped_column(Integer)
    titolo: Mapped[str] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(String(20))
    # Scadenza dell'operazione = inizio della fase + giorni_previsti
    giorni_previsti: Mapped[int | None] = mapped_column(Integer)
    documenti: Mapped[list[str] | None] = _lista_testi()
    campi: Mapped[list[str] | None] = _lista_testi()
    oggetto_email: Mapped[str | None] = mapped_column(Text)
    testo_email: Mapped[str | None] = mapped_column(Text)

    fase: Mapped[ModelloFase] = relationship(back_populates="operazioni")


# --- Pratiche ----------------------------------------------------------------------------


class Pratica(ConTimestamp, Base):
    __tablename__ = "pratica"
    __table_args__ = (
        _in("stato", STATI_PRATICA, "ck_pratica_stato"),
        CheckConstraint("bando_id IS NOT NULL OR bando_testo IS NOT NULL", name="ck_pratica_bando"),
    )

    codice: Mapped[str] = mapped_column(String(20), unique=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="RESTRICT"), index=True
    )
    bando_id: Mapped[int | None] = mapped_column(
        ForeignKey("bando.id", ondelete="SET NULL"), index=True
    )
    # Bando non presente nel database (es. avviso camerale non raccolto): solo il nome
    bando_testo: Mapped[str | None] = mapped_column(Text)
    modello_id: Mapped[int | None] = mapped_column(
        ForeignKey("modello_pratica.id", ondelete="SET NULL")
    )
    stato: Mapped[str] = mapped_column(String(20), default="in_preparazione")
    responsabile_id: Mapped[int | None] = mapped_column(
        ForeignKey("utente.id", ondelete="SET NULL"), index=True
    )
    importo_richiesto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    importo_concesso: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    protocollo: Mapped[str | None] = mapped_column(Text)
    scadenza_presentazione: Mapped[date | None] = mapped_column(Date, index=True)
    note: Mapped[str | None] = mapped_column(Text)

    azienda: Mapped[Azienda] = relationship()
    bando: Mapped[Bando | None] = relationship()
    modello: Mapped[ModelloPratica | None] = relationship()
    responsabile: Mapped[Utente | None] = relationship()
    fasi: Mapped[list["PraticaFase"]] = relationship(
        back_populates="pratica", cascade="all, delete-orphan", order_by="PraticaFase.numero"
    )
    eventi: Mapped[list["PraticaEvento"]] = relationship(
        back_populates="pratica",
        cascade="all, delete-orphan",
        order_by="PraticaEvento.creato_il.desc()",
    )

    @property
    def nome_bando(self) -> str:
        return self.bando.titolo if self.bando else (self.bando_testo or "—")

    @property
    def fase_attiva(self) -> "PraticaFase | None":
        return next((f for f in self.fasi if f.stato == "in_corso"), None)


class PraticaFase(ConTimestamp, Base):
    __tablename__ = "pratica_fase"
    __table_args__ = (_in("stato", STATI_FASE, "ck_pratica_fase_stato"),)

    pratica_id: Mapped[int] = mapped_column(
        ForeignKey("pratica.id", ondelete="CASCADE"), index=True
    )
    numero: Mapped[int] = mapped_column(Integer)
    nome: Mapped[str] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    stato: Mapped[str] = mapped_column(String(20), default="da_iniziare")
    iniziata_il: Mapped[date | None] = mapped_column(Date)
    completata_il: Mapped[date | None] = mapped_column(Date)

    pratica: Mapped[Pratica] = relationship(back_populates="fasi")
    operazioni: Mapped[list["PraticaOperazione"]] = relationship(
        back_populates="fase", cascade="all, delete-orphan", order_by="PraticaOperazione.numero"
    )


class PraticaOperazione(ConTimestamp, Base):
    __tablename__ = "pratica_operazione"
    __table_args__ = (
        _in("tipo", TIPI_OPERAZIONE, "ck_pratica_operazione_tipo"),
        _in("stato", STATI_OPERAZIONE, "ck_pratica_operazione_stato"),
    )

    fase_id: Mapped[int] = mapped_column(
        ForeignKey("pratica_fase.id", ondelete="CASCADE"), index=True
    )
    numero: Mapped[int] = mapped_column(Integer)
    titolo: Mapped[str] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(String(20))
    stato: Mapped[str] = mapped_column(String(20), default="da_fare")
    giorni_previsti: Mapped[int | None] = mapped_column(Integer)
    scadenza: Mapped[date | None] = mapped_column(Date, index=True)
    assegnata_a: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    completata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completata_da: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    oggetto_email: Mapped[str | None] = mapped_column(Text)
    testo_email: Mapped[str | None] = mapped_column(Text)

    fase: Mapped[PraticaFase] = relationship(back_populates="operazioni")
    assegnatario: Mapped[Utente | None] = relationship(foreign_keys=[assegnata_a])
    documenti: Mapped[list["PraticaDocumento"]] = relationship(
        back_populates="operazione", cascade="all, delete-orphan", order_by="PraticaDocumento.id"
    )
    dati: Mapped[list["PraticaDato"]] = relationship(
        back_populates="operazione", cascade="all, delete-orphan", order_by="PraticaDato.id"
    )


class PraticaDocumento(ConTimestamp, Base):
    """Voce della checklist documentale (il file vero arriverà con la tabella allegato)."""

    __tablename__ = "pratica_documento"
    __table_args__ = (_in("stato", STATI_DOCUMENTO, "ck_pratica_documento_stato"),)

    operazione_id: Mapped[int] = mapped_column(
        ForeignKey("pratica_operazione.id", ondelete="CASCADE"), index=True
    )
    nome: Mapped[str] = mapped_column(Text)
    stato: Mapped[str] = mapped_column(String(20), default="mancante")
    nota: Mapped[str | None] = mapped_column(Text)

    operazione: Mapped[PraticaOperazione] = relationship(back_populates="documenti")
    allegati: Mapped[list["Allegato"]] = relationship(order_by="Allegato.id", viewonly=True)


class PraticaDato(ConTimestamp, Base):
    __tablename__ = "pratica_dato"

    operazione_id: Mapped[int] = mapped_column(
        ForeignKey("pratica_operazione.id", ondelete="CASCADE"), index=True
    )
    etichetta: Mapped[str] = mapped_column(Text)
    valore: Mapped[str | None] = mapped_column(Text)

    operazione: Mapped[PraticaOperazione] = relationship(back_populates="dati")


class PraticaEvento(Base):
    """Diario della pratica: cambi di stato e di fase, operazioni completate."""

    __tablename__ = "pratica_evento"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pratica_id: Mapped[int] = mapped_column(
        ForeignKey("pratica.id", ondelete="CASCADE"), index=True
    )
    da_stato: Mapped[str | None] = mapped_column(String(20))
    a_stato: Mapped[str | None] = mapped_column(String(20))
    nota: Mapped[str] = mapped_column(Text)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    creato_il: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pratica: Mapped[Pratica] = relationship(back_populates="eventi")
    utente: Mapped[Utente | None] = relationship()


# --- Allegati, prefattibilità, email inviate ----------------------------------------------

ESITI_PREFATTIBILITA = ("da_valutare", "fattibile", "fattibile_con_riserva", "non_fattibile")
TIPI_NOTIFICA = ("comunicazione_pratica", "promemoria", "prova")


class Allegato(ConTimestamp, Base):
    """File caricato (schema F1). Il file sta su disco con un nome casuale; qui i metadati."""

    __tablename__ = "allegato"

    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    pratica_id: Mapped[int | None] = mapped_column(
        ForeignKey("pratica.id", ondelete="CASCADE"), index=True
    )
    pratica_documento_id: Mapped[int | None] = mapped_column(
        ForeignKey("pratica_documento.id", ondelete="SET NULL"), index=True
    )
    nome_originale: Mapped[str] = mapped_column(Text)
    percorso: Mapped[str] = mapped_column(Text, unique=True)
    mime: Mapped[str] = mapped_column(String(120))
    dimensione: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    caricato_da: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))

    utente: Mapped[Utente | None] = relationship()


class Prefattibilita(ConTimestamp, Base):
    """Verifica preliminare: il cliente può partecipare al bando? Voci in JSON:
    [{"requisito", "esito": ok|ko|da_verificare, "nota", "automatica": bool}]."""

    __tablename__ = "prefattibilita"
    __table_args__ = (
        _in("esito", ESITI_PREFATTIBILITA, "ck_prefattibilita_esito"),
        CheckConstraint(
            "bando_id IS NOT NULL OR bando_testo IS NOT NULL", name="ck_prefattibilita_bando"
        ),
    )

    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    bando_id: Mapped[int | None] = mapped_column(ForeignKey("bando.id", ondelete="SET NULL"))
    bando_testo: Mapped[str | None] = mapped_column(Text)
    esito: Mapped[str] = mapped_column(String(30), default="da_valutare")
    voci: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    importo_progetto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    contributo_stimato: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    note: Mapped[str | None] = mapped_column(Text)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    pratica_id: Mapped[int | None] = mapped_column(ForeignKey("pratica.id", ondelete="SET NULL"))

    azienda: Mapped[Azienda] = relationship()
    bando: Mapped[Bando | None] = relationship()
    utente: Mapped[Utente | None] = relationship()
    pratica: Mapped[Pratica | None] = relationship()

    @property
    def nome_bando(self) -> str:
        return self.bando.titolo if self.bando else (self.bando_testo or "—")


class NotificaEmail(Base):
    """Registro di ogni email inviata (o tentata) dal server (schema F5)."""

    __tablename__ = "notifica_email"
    __table_args__ = (
        _in("tipo", TIPI_NOTIFICA, "ck_notifica_email_tipo"),
        _in("esito", ("ok", "errore"), "ck_notifica_email_esito"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(30))
    destinatario: Mapped[str] = mapped_column(String(254))
    oggetto: Mapped[str] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)
    pratica_id: Mapped[int | None] = mapped_column(
        ForeignKey("pratica.id", ondelete="SET NULL"), index=True
    )
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    esito: Mapped[str] = mapped_column(String(10))
    errore: Mapped[str | None] = mapped_column(Text)
    inviata_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
