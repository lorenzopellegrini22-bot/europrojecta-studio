"""Registro Imprese tramite il servizio "Company" di Openapi S.p.A.

Verificato sulla specifica OpenAPI ufficiale (console.openapi.com/oas/it/company.openapi.json,
18/11/2026):
- server: produzione https://company.openapi.com, sandbox https://test.company.openapi.com;
- autenticazione: header `Authorization: Bearer <token>`;
- GET /IT-start/{vatCode_taxCode_or_id}: anagrafica, sede legale, stato attività, codice SDI;
- GET /IT-advanced/{vatCode_taxCode_or_id}: in più ATECO, forma giuridica, PEC, bilanci;
- risposta {"data": [ … ], "success", "message", "error"};
- errori: 400 identificativo mancante, 402 credito insufficiente, 404 azienda non trovata,
  406 identificativo non valido; 401/403 token non valido o senza lo scope richiesto.
Esce solo l'identificativo cercato (vedi docs/dati-in-uscita.md).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from app.sources.http import USER_AGENT
from app.utils.territorio import REGIONI

SERVER = {
    "produzione": "https://company.openapi.com",
    "sandbox": "https://test.company.openapi.com",
}
ENDPOINT = {"start": "IT-start", "advanced": "IT-advanced"}
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class ErroreRegistro(Exception):
    """Messaggio già pensato per il consulente."""


@dataclass
class DatiImpresa:
    ragione_sociale: str | None = None
    partita_iva: str | None = None
    codice_fiscale: str | None = None
    forma_giuridica: str | None = None
    pec: str | None = None
    codice_sdi: str | None = None
    indirizzo: str | None = None
    cap: str | None = None
    comune_testo: str | None = None
    provincia: str | None = None
    regione: str | None = None
    stato_attivita: str | None = None
    ateco_codice: str | None = None
    ateco_descrizione: str | None = None
    dipendenti: int | None = None
    fatturato: Decimal | None = None
    anno_dati: int | None = None
    avvisi: list[str] = field(default_factory=list)

    def campi_compilati(self) -> dict[str, Any]:
        return {
            k: v for k, v in self.__dict__.items() if v not in (None, "") and k not in ("avvisi",)
        }


def _norm(testo: str) -> str:
    senza_accenti = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", senza_accenti.lower())


def regione_da_testo(testo: str | None) -> str | None:
    """«LAZIO» → «Lazio»; «TRENTINO-ALTO ADIGE» → «Trentino-Alto Adige/Südtirol»."""
    if not testo:
        return None
    n = _norm(testo.split("/")[0])
    if not n:
        return None
    for r in REGIONI:
        if _norm(r.split("/")[0]) == n:
            return r
    return None


def _testo(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _ateco_puntato(codice: str) -> str:
    """«6201» → «62.01», «621» → «62.1» (formato ISTAT con il punto dopo la divisione)."""
    c = codice.replace(".", "").strip()
    return f"{c[:2]}.{c[2:]}" if len(c) > 2 and c.isdigit() else c


def interpreta(record: dict[str, Any]) -> DatiImpresa:
    sede = ((record.get("address") or {}).get("registeredOffice")) or {}
    forma = record.get("detailedLegalForm") or {}
    d = DatiImpresa(
        ragione_sociale=_testo(record.get("companyName")),
        partita_iva=_testo(record.get("vatCode")),
        codice_fiscale=_testo(record.get("taxCode")),
        forma_giuridica=_testo(forma.get("description")),
        pec=_testo(record.get("pec")),
        codice_sdi=_testo(record.get("sdiCode")),
        indirizzo=_testo(sede.get("streetName")),
        cap=_testo(sede.get("zipCode")),
        comune_testo=_testo(sede.get("town")),
        provincia=_testo(sede.get("province")),
        regione=regione_da_testo((sede.get("region") or {}).get("description")),
        stato_attivita=_testo(record.get("activityStatus")),
    )
    if d.comune_testo:
        d.comune_testo = d.comune_testo.title()
    if d.forma_giuridica:
        d.forma_giuridica = d.forma_giuridica.capitalize()
    if d.partita_iva and not re.fullmatch(r"\d{11}", d.partita_iva):
        d.partita_iva = None
    if d.codice_sdi and len(d.codice_sdi) != 7:
        d.codice_sdi = None
    # ATECO: si usa la classificazione 2007, l'unica con una versione esplicita e certa
    ateco = ((record.get("atecoClassification") or {}).get("ateco2007")) or {}
    if ateco.get("code"):
        d.ateco_codice = _ateco_puntato(str(ateco["code"]))
        d.ateco_descrizione = _testo(ateco.get("description"))
    ultimo = ((record.get("balanceSheets") or {}).get("last")) or {}
    if isinstance(ultimo.get("employees"), int):
        d.dipendenti = ultimo["employees"]
    if isinstance(ultimo.get("turnover"), int | float):
        d.fatturato = Decimal(str(ultimo["turnover"]))
    if isinstance(ultimo.get("year"), int):
        d.anno_dati = ultimo["year"]
    if d.stato_attivita and d.stato_attivita.upper() != "ATTIVA":
        d.avvisi.append(f"Stato dell'attività al Registro Imprese: {d.stato_attivita}.")
    if record.get("cessata") is True or record.get("endDate"):
        d.avvisi.append("Il Registro Imprese indica l'azienda come cessata.")
    return d


def cerca(
    valori: dict[str, str | None],
    identificativo: str,
    client: httpx.Client | None = None,
) -> DatiImpresa:
    """Cerca un'azienda per partita IVA o codice fiscale. Solleva ErroreRegistro."""
    token = valori.get("token")
    if not token:
        raise ErroreRegistro("Manca il token di Openapi: inseriscilo nelle impostazioni.")
    ident = re.sub(r"\s", "", identificativo or "").upper().removeprefix("IT")
    if not re.fullmatch(r"\d{11}|[A-Z0-9]{16}", ident):
        raise ErroreRegistro("Inserisci una partita IVA (11 cifre) o un codice fiscale valido.")
    base = SERVER.get(valori.get("ambiente") or "sandbox", SERVER["sandbox"])
    endpoint = ENDPOINT.get(valori.get("livello") or "advanced", ENDPOINT["advanced"])
    proprio = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = client.get(
            f"{base}/{endpoint}/{ident}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        )
    except httpx.HTTPError as e:
        raise ErroreRegistro("Impossibile raggiungere Openapi: problema di rete dal server.") from e
    finally:
        if proprio:
            client.close()
    if r.status_code in (401, 403):
        raise ErroreRegistro(
            "Token non valido, scaduto o senza lo scope per questo servizio: controlla gli "
            f"scope GET:{base.removeprefix('https://')}/{endpoint} nella console di Openapi."
        )
    if r.status_code == 402:
        raise ErroreRegistro("Credito Openapi esaurito: ricarica il borsellino nella console.")
    if r.status_code in (204, 404):
        raise ErroreRegistro(
            "Nessuna azienda trovata con questo identificativo"
            + (" (in sandbox rispondono solo le aziende di esempio)." if "test." in base else ".")
        )
    if r.status_code in (400, 406):
        raise ErroreRegistro("Openapi considera non valido l'identificativo inserito.")
    if r.status_code >= 400:
        raise ErroreRegistro(f"Openapi ha risposto con un errore ({r.status_code}).")
    try:
        corpo = r.json()
    except ValueError as e:
        raise ErroreRegistro("Risposta di Openapi non leggibile.") from e
    dati = corpo.get("data") if isinstance(corpo, dict) else None
    if isinstance(dati, dict):
        dati = [dati]
    if not dati or not isinstance(dati[0], dict):
        raise ErroreRegistro("Nessuna azienda trovata con questo identificativo.")
    return interpreta(dati[0])
