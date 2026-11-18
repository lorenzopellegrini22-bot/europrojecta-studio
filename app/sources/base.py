"""Interfaccia comune delle fonti di bandi.

Ogni fonte è un modulo in app/sources/ che espone un oggetto con:
- codice, nome, url
- versione_remota(http) -> str | None   (es. Last-Modified, per saltare file invariati)
- scarica(http) -> Path                  (file grezzo completo)
- interpreta(percorso) -> list[RecordBando]
Il runner comune (app/sources/runner.py) fa il resto: confronto, versioni, chiusure, log.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx


@dataclass
class RequisitiFonte:
    """Requisiti già strutturati dalla fonte (provenienza "fonte"), senza passare dall'LLM.
    None = non noto / nessun vincolo noto. Le liste non sono mai vuote."""

    territorio: str = "sconosciuto"
    regioni: list[str] | None = None
    province: list[str] | None = None
    comuni: list[str] | None = None
    aree_speciali: list[str] | None = None
    dimensioni: list[str] | None = None
    ateco_ammessi: list[str] | None = None
    ateco_versione: str | None = None
    tipologie_soggetto: list[str] | None = None
    settori: list[str] | None = None
    forma_agevolazione: list[str] | None = None
    finalita: list[str] | None = None
    importo_min: Decimal | None = None
    importo_max: Decimal | None = None
    spesa_min: Decimal | None = None
    spesa_max: Decimal | None = None
    dotazione: Decimal | None = None
    da_verificare: list[str] = field(default_factory=list)


@dataclass
class RecordBando:
    id_esterno: str
    titolo: str
    raw: dict[str, Any]
    id_rna: str | None = None
    ente: str | None = None
    descrizione: str | None = None
    link_ufficiale: str | None = None
    link_domande: str | None = None
    data_apertura: date | None = None
    data_chiusura: date | None = None
    note_apertura_chiusura: str | None = None
    requisiti: RequisitiFonte = field(default_factory=RequisitiFonte)


class Fonte(Protocol):
    codice: str
    nome: str
    url: str

    def versione_remota(self, http: httpx.Client) -> str | None: ...

    def scarica(self, http: httpx.Client) -> Path: ...

    def interpreta(self, percorso: Path) -> list[RecordBando]: ...
