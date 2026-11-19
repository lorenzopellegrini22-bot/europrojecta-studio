"""Tutti i modelli: importarli qui li registra su Base.metadata (serve ad Alembic e ai test)."""

from app.models.ai import BandoOverride, Embedding, EstrazioneBando, ImpostazioniAI, UsoLLM
from app.models.appalti import Appalto
from app.models.auth import AuditLog, CodiceRecupero, LogAccesso, Sessione, Utente
from app.models.bandi import Bando, BandoRequisiti, BandoVersione, Fonte, FonteRun
from app.models.collaborazione import Commento, Menzione, NovitaSeguito, Seguito
from app.models.crm import Azienda, AziendaAteco, Referente, Sede, Segnalatore
from app.models.crm_esteso import (
    AggiornamentoCliente,
    AiutoDeMinimis,
    Attivita,
    ImportJob,
    MappaturaImport,
    Progetto,
    ReportBando,
    Scadenza,
    Tag,
)
from app.models.documenti import DocumentoBando
from app.models.impostazioni import Integrazione
from app.models.jobs import JobRun
from app.models.opportunita import Opportunita
from app.models.pratiche import (
    Allegato,
    AllegatoContenuto,
    ModelloFase,
    ModelloOperazione,
    ModelloPratica,
    NotificaEmail,
    Pratica,
    PraticaDato,
    PraticaDocumento,
    PraticaEvento,
    PraticaFase,
    PraticaOperazione,
    Prefattibilita,
)
from app.models.riferimenti import AtecoRaccordo, Comune

__all__ = [
    "Commento",
    "Menzione",
    "NovitaSeguito",
    "Seguito",
    "AtecoRaccordo",
    "Comune",
    "AggiornamentoCliente",
    "AiutoDeMinimis",
    "Attivita",
    "ImportJob",
    "MappaturaImport",
    "Progetto",
    "ReportBando",
    "Scadenza",
    "Tag",
    "Embedding",
    "DocumentoBando",
    "UsoLLM",
    "ImpostazioniAI",
    "EstrazioneBando",
    "BandoOverride",
    "Opportunita",
    "Appalto",
    "Segnalatore",
    "Prefattibilita",
    "NotificaEmail",
    "AziendaAteco",
    "Allegato",
    "AllegatoContenuto",
    "AuditLog",
    "Azienda",
    "Bando",
    "BandoRequisiti",
    "BandoVersione",
    "CodiceRecupero",
    "Fonte",
    "FonteRun",
    "Integrazione",
    "JobRun",
    "LogAccesso",
    "ModelloFase",
    "ModelloOperazione",
    "ModelloPratica",
    "Pratica",
    "PraticaDato",
    "PraticaDocumento",
    "PraticaEvento",
    "PraticaFase",
    "PraticaOperazione",
    "Referente",
    "Sede",
    "Sessione",
    "Utente",
]
