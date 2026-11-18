"""Caricamento dei file ufficiali ISTAT (verificati il 18/11/2026).

- Comuni: pagina «Codici dei comuni, delle province e delle regioni»
  (istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/). Il link xlsx
  è permanente: ISTAT lo aggiorna mantenendo lo stesso indirizzo. Foglio «CODICI al …»,
  una riga per comune (7.895 al 21/02/2026).
- Raccordo ATECO: «Tavola di raccordo bidirezionale ATECO 2025 – ATECO 2022», aggiornamento
  del 15/07/2026 (istat.it/classificazione/documenti-ateco/). «ATECO 2022» è l'ATECO 2007
  aggiornamento 2022, cioè la versione «2007» usata nell'app. Foglio «ATECO 2025 vs ATECO
  2022», colonne CODICE_ATECO_2025, CODICE_ATECO_2022, COPERTURA_*.

robots.txt di istat.it non vieta questi file. Le colonne si cercano per nome (non per
posizione), così un riordino del file non rompe il caricamento.
"""

import io
import logging
from typing import Any

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import AtecoRaccordo, Comune
from app.utils.ricerca import normalizza

log = logging.getLogger(__name__)

URL_COMUNI = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.xlsx"
URL_RACCORDO = (
    "https://www.istat.it/wp-content/uploads/2026/07/"
    "Aggiornamento-2026-Tavola-raccordo-bidirezionale-ATECO-2025-ATECO-2022-italiano.xlsx"
)
USER_AGENT = "EuroprojectaStudio/1.0 (gestionale bandi; tabelle di riferimento ISTAT)"


class ErroreIstat(Exception):
    pass


def norm_nome(nome: str) -> str:
    """Nome confrontabile: minuscolo, senza accenti, apostrofi, trattini e spazi doppi
    («Sant'Angelo», «Sant Angelo», «SANT’ANGELO» → «sant angelo»)."""
    n = normalizza(nome or "").replace("’", " ").replace("'", " ").replace("-", " ")
    return " ".join(n.split())


def _righe(dati: bytes, foglio_che_inizia: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
    ws = next((w for w in wb.worksheets if w.title.startswith(foglio_che_inizia)), None)
    if ws is None:
        raise ErroreIstat(f"Foglio «{foglio_che_inizia}…» non trovato nel file ISTAT.")
    righe = list(ws.iter_rows(values_only=True))
    intestazioni = [str(h or "").strip() for h in righe[0]]
    return intestazioni, righe[1:]


def _colonna(intestazioni: list[str], inizio: str) -> int:
    for i, h in enumerate(intestazioni):
        if h.lower().startswith(inizio.lower()):
            return i
    raise ErroreIstat(f"Colonna «{inizio}…» non trovata: il formato del file ISTAT è cambiato.")


def leggi_comuni(dati: bytes) -> list[dict[str, Any]]:
    intestazioni, righe = _righe(dati, "CODICI")
    c_codice = _colonna(intestazioni, "Codice Comune formato alfanumerico")
    c_nome = _colonna(intestazioni, "Denominazione in italiano")
    c_regione = _colonna(intestazioni, "Denominazione Regione")
    c_prov = _colonna(intestazioni, "Denominazione dell'Unit")
    c_sigla = _colonna(intestazioni, "Sigla automobilistica")
    c_catasto = _colonna(intestazioni, "Codice Catastale")
    out = []
    for r in righe:
        codice = str(r[c_codice] or "").strip()
        if not codice:
            continue
        nome = str(r[c_nome]).strip()
        out.append(
            {
                "codice_istat": codice.zfill(6),
                "nome": nome,
                "nome_norm": norm_nome(nome),
                "sigla_provincia": str(r[c_sigla] or "").strip().upper(),
                "provincia": str(r[c_prov] or "").strip(),
                "regione": str(r[c_regione] or "").strip(),
                "codice_catastale": str(r[c_catasto] or "").strip() or None,
            }
        )
    if len(out) < 7000:
        raise ErroreIstat(f"Solo {len(out)} comuni nel file: troppo pochi, non lo carico.")
    return out


def leggi_raccordo(dati: bytes) -> list[dict[str, Any]]:
    intestazioni, righe = _righe(dati, "ATECO 2025 vs")
    c25 = _colonna(intestazioni, "CODICE_ATECO_2025")
    c07 = _colonna(intestazioni, "CODICE_ATECO_2022")
    cop25 = _colonna(intestazioni, "COPERTURA_ATECO_2025")
    cop07 = _colonna(intestazioni, "COPERTURA_ATECO_2022")
    out = []
    for r in righe:
        a, b = str(r[c25] or "").strip(), str(r[c07] or "").strip()
        if a and b:
            out.append(
                {
                    "codice_2025": a,
                    "codice_2007": b,
                    "copertura_2025": (r[cop25] or None),
                    "copertura_2007": (r[cop07] or None),
                }
            )
    if len(out) < 5000:
        raise ErroreIstat(f"Solo {len(out)} righe di raccordo: troppo poche, non le carico.")
    return out


def carica(
    db: Session, comuni: list[dict[str, Any]] | None, raccordo: list[dict[str, Any]] | None
) -> str:
    """Sostituisce le tabelle (in un'unica transazione)."""
    parti = []
    if comuni is not None:
        db.execute(delete(Comune))
        db.bulk_insert_mappings(Comune, comuni)  # type: ignore[arg-type]
        parti.append(f"{len(comuni)} comuni")
    if raccordo is not None:
        db.execute(delete(AtecoRaccordo))
        db.bulk_insert_mappings(AtecoRaccordo, raccordo)  # type: ignore[arg-type]
        parti.append(f"{len(raccordo)} righe di raccordo ATECO")
    db.commit()
    from app.riferimenti import ateco, territorio

    territorio.azzera_cache()
    ateco.azzera_cache()
    return "Tabelle ISTAT caricate: " + ", ".join(parti) + "."


def scarica(url: str, client: httpx.Client | None = None) -> bytes:
    c = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        r = c.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as e:
        raise ErroreIstat(f"Download non riuscito ({url}): {type(e).__name__}") from e
    finally:
        if client is None:
            c.close()


def aggiorna(db: Session, client: httpx.Client | None = None) -> str:
    """Scarica e carica entrambi i file (job mensile e comando carica-istat)."""
    comuni = leggi_comuni(scarica(URL_COMUNI, client))
    raccordo = leggi_raccordo(scarica(URL_RACCORDO, client))
    return carica(db, comuni, raccordo)
