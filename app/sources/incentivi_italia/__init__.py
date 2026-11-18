"""Fonte nazionale: open data di Incentivi Italia (licenza CC BY 4.0).

Verifica del 18/11/2026 in docs/fonti/incentivi-italia.md. Punti chiave:
- si usa il JSON (liste e numeri già tipizzati), non il CSV;
- aggiornamento settimanale: se Last-Modified non cambia, non si riscarica;
- il server tronca i download: scarica_completo riprende con Range;
- "Ambito_territoriale" contiene zone speciali (ZES, Aree interne…), non la scala territoriale.
"""

import json
from pathlib import Path

import httpx

from app.sources import http
from app.sources.base import RecordBando
from app.sources.incentivi_italia.parser import interpreta_record

CODICE = "incentivi_italia"
NOME = "Incentivi Italia (open data nazionale)"
URL = "https://www.incentivitalia.gov.it/open-data/incentivi/OpenData_Incentivi.json"
ATTRIBUZIONE = (
    "Dati: Incentivi Italia (incentivitalia.gov.it), licenza CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/). Dati rielaborati."
)


class ErroreFormato(Exception):
    pass


class IncentiviItalia:
    codice = CODICE
    nome = NOME
    url = URL

    def versione_remota(self, client: httpx.Client) -> str | None:
        return http.intestazioni(client, self.url).last_modified

    def scarica(self, client: httpx.Client) -> Path:
        return http.scarica_completo(client, self.url)

    def interpreta(self, percorso: Path) -> list[RecordBando]:
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ErroreFormato(f"Il file non è un JSON valido: {e}") from e
        if not isinstance(dati, list):
            raise ErroreFormato("Atteso un array di record")
        record = [interpreta_record(r) for r in dati if isinstance(r, dict)]
        return [r for r in record if r is not None]


FONTE = IncentiviItalia()
