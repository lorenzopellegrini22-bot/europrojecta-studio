"""CRM completo (brief, sezione 5): progetti d'investimento, attività, scadenze, de minimis,
tag, import da CSV, aggiornamenti ai clienti e report sui bandi."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, ConTimestamp
from app.models.crm import Azienda, Referente

# Vocabolario chiuso condiviso da progetti e bandi (brief, sezione 5: categoria_spesa).
# È lo stesso usato dall'estrazione AI per le spese ammissibili.
CATEGORIE_SPESA = (
    "macchinari e attrezzature",
    "digitale e software",
    "ricerca e sviluppo",
    "energia e ambiente",
    "formazione",
    "assunzioni",
    "internazionalizzazione",
    "consulenze",
    "immobili e opere",
    "avvio d'impresa",
    "brevetti e proprietà intellettuale",
    "altro",
)
STATI_PROGETTO = ("idea", "in_valutazione", "in_corso", "concluso", "abbandonato")
TIPI_ATTIVITA = ("nota", "chiamata", "email", "incontro")
REGOLAMENTI_DE_MINIMIS = ("generale", "agricolo", "pesca", "sieg")
FREQUENZE_AGGIORNAMENTO = ("nessuno", "settimanale", "mensile")
CANALI = ("email", "whatsapp")
STATI_AGGIORNAMENTO = ("bozza", "approvato", "inviato", "errore", "scartato")
STATI_REPORT = ("bozza", "approvato", "inviato")
# Automazioni (sezione Automazioni): quando partono, per chi, cosa fanno
EVENTI_AUTOMAZIONE = ("nuovo_bando", "scadenza", "riepilogo")
DESTINATARI_AUTOMAZIONE = ("tutti", "imprese", "enti", "scelti")
AZIONI_AUTOMAZIONE = ("bozza", "invio")
CANALI_AUTOMAZIONE = ("preferito", "email", "whatsapp")


def _in(valori: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in valori) + ")"


azienda_tag = Table(
    "azienda_tag",
    Base.metadata,
    Column("azienda_id", ForeignKey("azienda.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(60), unique=True)


class Progetto(ConTimestamp, Base):
    """Progetto d'investimento di un'azienda: è ciò che si confronta con i bandi."""

    __tablename__ = "progetto"
    __table_args__ = (CheckConstraint(f"stato IN {_in(STATI_PROGETTO)}", name="ck_progetto_stato"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    titolo: Mapped[str] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    importo_stimato: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    inizio_previsto: Mapped[date | None] = mapped_column(Date)
    fine_prevista: Mapped[date | None] = mapped_column(Date)
    stato: Mapped[str] = mapped_column(String(20), default="idea")
    # Valori di CATEGORIE_SPESA (vocabolario chiuso, controllato dall'applicazione)
    categorie: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    azienda: Mapped[Azienda] = relationship()

    @property
    def attivo(self) -> bool:
        return self.stato in ("idea", "in_valutazione", "in_corso")


class Attivita(ConTimestamp, Base):
    """Nota, chiamata, email o incontro con un'azienda (storico del CRM)."""

    __tablename__ = "attivita"
    __table_args__ = (CheckConstraint(f"tipo IN {_in(TIPI_ATTIVITA)}", name="ck_attivita_tipo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    progetto_id: Mapped[int | None] = mapped_column(ForeignKey("progetto.id", ondelete="SET NULL"))
    pratica_id: Mapped[int | None] = mapped_column(ForeignKey("pratica.id", ondelete="SET NULL"))
    bando_id: Mapped[int | None] = mapped_column(ForeignKey("bando.id", ondelete="SET NULL"))
    tipo: Mapped[str] = mapped_column(String(20))
    data: Mapped[date] = mapped_column(Date)
    testo: Mapped[str] = mapped_column(Text)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))


class Scadenza(ConTimestamp, Base):
    """Promemoria legato a un'azienda (le scadenze delle pratiche stanno nelle operazioni)."""

    __tablename__ = "scadenza"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    progetto_id: Mapped[int | None] = mapped_column(ForeignKey("progetto.id", ondelete="SET NULL"))
    pratica_id: Mapped[int | None] = mapped_column(ForeignKey("pratica.id", ondelete="SET NULL"))
    bando_id: Mapped[int | None] = mapped_column(ForeignKey("bando.id", ondelete="SET NULL"))
    titolo: Mapped[str] = mapped_column(Text)
    data: Mapped[date] = mapped_column(Date, index=True)
    completata: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    completata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    azienda: Mapped[Azienda] = relationship()


class AiutoDeMinimis(ConTimestamp, Base):
    __tablename__ = "aiuto_de_minimis"
    __table_args__ = (
        CheckConstraint(
            f"regolamento IN {_in(REGOLAMENTI_DE_MINIMIS)}", name="ck_aiuto_de_minimis_regolamento"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    data_concessione: Mapped[date] = mapped_column(Date)
    importo: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    regolamento: Mapped[str] = mapped_column(String(10), default="generale")
    ente: Mapped[str | None] = mapped_column(Text)
    descrizione: Mapped[str | None] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(String(20), default="manuale", server_default="manuale")


class ImportJob(ConTimestamp, Base):
    """Import dei clienti da CSV (export del CRM precedente)."""

    __tablename__ = "import_job"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome_file: Mapped[str] = mapped_column(Text)
    contenuto: Mapped[str] = mapped_column(Text)  # testo del CSV già decodificato
    separatore: Mapped[str] = mapped_column(String(1))
    codifica: Mapped[str] = mapped_column(String(20))
    stato: Mapped[str] = mapped_column(String(20), default="caricato")
    mappatura: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    righe_totali: Mapped[int] = mapped_column(Integer, default=0)
    righe_importate: Mapped[int] = mapped_column(Integer, default=0)
    righe_scartate: Mapped[int] = mapped_column(Integer, default=0)
    errori: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))


class MappaturaImport(ConTimestamp, Base):
    """Mappatura colonna → campo salvata per riusarla con lo stesso tipo di file."""

    __tablename__ = "mappatura_import"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    mappatura: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AggiornamentoCliente(ConTimestamp, Base):
    """Aggiornamento periodico a un'azienda cliente: nasce bozza, il consulente lo approva."""

    __tablename__ = "aggiornamento_cliente"
    __table_args__ = (
        CheckConstraint(f"canale IN {_in(CANALI)}", name="ck_aggiornamento_cliente_canale"),
        CheckConstraint(
            f"stato IN {_in(STATI_AGGIORNAMENTO)}", name="ck_aggiornamento_cliente_stato"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(
        ForeignKey("azienda.id", ondelete="CASCADE"), index=True
    )
    referente_id: Mapped[int | None] = mapped_column(
        ForeignKey("referente.id", ondelete="SET NULL")
    )
    canale: Mapped[str] = mapped_column(String(10), default="email")
    oggetto: Mapped[str] = mapped_column(Text)
    testo: Mapped[str] = mapped_column(Text)
    bandi_citati: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    pratiche_citate: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    stato: Mapped[str] = mapped_column(String(20), default="bozza", index=True)
    generato_con_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    approvato_da_id: Mapped[int | None] = mapped_column(
        ForeignKey("utente.id", ondelete="SET NULL")
    )
    approvato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inviato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    id_messaggio: Mapped[str | None] = mapped_column(Text)
    errore: Mapped[str | None] = mapped_column(Text)
    # Creato da un'automazione (None: a mano o dalla frequenza del cliente)
    automazione_id: Mapped[int | None] = mapped_column(
        ForeignKey("automazione.id", ondelete="SET NULL"), index=True
    )
    appalti_citati: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))

    azienda: Mapped[Azienda] = relationship()
    referente: Mapped[Referente | None] = relationship()
    automazione: Mapped["Automazione | None"] = relationship()


class Automazione(ConTimestamp, Base):
    """Regola della sezione Automazioni: quando succede qualcosa (nuovo bando adatto, bando
    in scadenza, riepilogo periodico) prepara o invia un messaggio ai clienti scelti."""

    __tablename__ = "automazione"
    __table_args__ = (
        CheckConstraint(f"evento IN {_in(EVENTI_AUTOMAZIONE)}", name="ck_automazione_evento"),
        CheckConstraint(
            f"destinatari IN {_in(DESTINATARI_AUTOMAZIONE)}", name="ck_automazione_destinatari"
        ),
        CheckConstraint(f"azione IN {_in(AZIONI_AUTOMAZIONE)}", name="ck_automazione_azione"),
        CheckConstraint(f"canale IN {_in(CANALI_AUTOMAZIONE)}", name="ck_automazione_canale"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(200))
    attiva: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # da quando è attiva: le opportunità trovate prima non fanno partire messaggi arretrati
    attiva_dal: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evento: Mapped[str] = mapped_column(String(20))
    giorni_scadenza: Mapped[int | None] = mapped_column(Integer)  # evento «scadenza»
    frequenza: Mapped[str | None] = mapped_column(String(20))  # evento «riepilogo»
    destinatari: Mapped[str] = mapped_column(String(10), default="tutti")
    aziende_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    ambiti: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # None = tutti
    punteggio_minimo: Mapped[int] = mapped_column(Integer, default=70)
    azione: Mapped[str] = mapped_column(String(10), default="bozza")
    canale: Mapped[str] = mapped_column(String(10), default="preferito")
    oggetto: Mapped[str] = mapped_column(Text)
    testo: Mapped[str] = mapped_column(Text)
    usa_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # chi la gestisce: i messaggi inviati da soli partono a suo nome (firma e casella email)
    utente_id: Mapped[int | None] = mapped_column(ForeignKey("utente.id", ondelete="SET NULL"))
    ultima_esecuzione: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_esito: Mapped[str | None] = mapped_column(Text)


class ReportBando(ConTimestamp, Base):
    """Report PDF su un bando per un'azienda cliente: bozza modificabile → approvato → PDF."""

    __tablename__ = "report_bando"
    __table_args__ = (
        CheckConstraint(f"stato IN {_in(STATI_REPORT)}", name="ck_report_bando_stato"),
        UniqueConstraint("azienda_id", "bando_id", name="uq_report_bando_azienda_bando"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    azienda_id: Mapped[int] = mapped_column(ForeignKey("azienda.id", ondelete="CASCADE"))
    bando_id: Mapped[int] = mapped_column(ForeignKey("bando.id", ondelete="CASCADE"))
    # Sezioni modificabili: sintesi, perche_adatto, cosa_serve, scadenze, note
    contenuto: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stato: Mapped[str] = mapped_column(String(20), default="bozza")
    approvato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pdf_generato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    azienda: Mapped[Azienda] = relationship()
