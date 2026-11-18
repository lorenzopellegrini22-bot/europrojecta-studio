"""Importazione dei file open data ANAC (Banca Dati Nazionale dei Contratti Pubblici).

Il portale dati.anticorruzione.it non consente lo scarico automatico dal server (rifiuta le
richieste che non vengono da un browser), quindi il consulente scarica i file dal browser e li
carica qui (licenza dei dati: CC BY-SA 4.0, vedi la pagina di ogni dataset).

File accettati (CSV oppure lo ZIP così come scaricato):
- "CIG" (dataset annuale "CIG anno …" o "CIG aggiornamenti delta"): una riga per CIG con
  oggetto, importi, scadenza delle offerte, procedura, stazione appaltante, CPV, provincia;
- "categorie-opera": le categorie SOA di ogni CIG (prevalente e scorporabili).
Le colonne si riconoscono dall'intestazione (nomi documentati da ANAC: cig, oggetto_gara,
oggetto_lotto, importo_lotto, data_scadenza_offerta, …), senza dipendere dall'ordine.
Del file CIG si tengono solo le gare con scadenza delle offerte non ancora passata.
"""

import csv
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import IO, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.appalti.scheda import CATEGORIE_SOA, unisci_soa
from app.integrazioni.registro_imprese import regione_da_testo
from app.models.appalti import Appalto
from app.utils.cpv import DIVISIONI_CPV
from app.utils.numeri import importo_italiano
from app.utils.nuts_italia import NUTS3_PROVINCIA

csv.field_size_limit(10 * 1024 * 1024)
NATURA = {"LAVORI": "lavori", "SERVIZI": "servizi", "FORNITURE": "forniture"}
PROVINCE = {nome.lower(): nome for nome, _ in NUTS3_PROVINCIA.values()}


class ErroreImport(Exception):
    pass


@dataclass
class EsitoImport:
    tipo: str
    lette: int = 0
    importate: int = 0
    aggiornate: int = 0
    scartate: int = 0
    avvisi: list[str] = field(default_factory=list)

    def riepilogo(self) -> str:
        if self.tipo == "cig":
            return (
                f"File CIG: {self.lette} righe lette, {self.importate} gare aperte importate, "
                f"{self.aggiornate} aggiornate, {self.scartate} già scadute o senza scadenza."
            )
        return (
            f"File categorie: {self.lette} righe lette, categorie SOA aggiunte a "
            f"{self.aggiornate} gare già presenti; {self.scartate} righe di gare non presenti."
        )


class _Catena(io.RawIOBase):
    """Flusso binario: prima i byte già letti, poi il resto del file."""

    def __init__(self, testa: bytes, coda: IO[bytes]):
        self.testa = testa
        self.coda = coda

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self.testa:
            n = min(len(buffer), len(self.testa))
            buffer[:n] = self.testa[:n]
            self.testa = self.testa[n:]
            return n
        dati = self.coda.read(len(buffer))
        buffer[: len(dati)] = dati
        return len(dati)


def _righe(sorgente: IO[bytes], nome: str) -> Iterator[dict[str, str]]:
    """Righe del CSV (anche dentro uno ZIP), con intestazioni in minuscolo."""
    dati: IO[bytes] = sorgente
    if nome.lower().endswith(".zip"):
        try:
            archivio = zipfile.ZipFile(sorgente)
        except zipfile.BadZipFile as e:
            raise ErroreImport("Lo ZIP non è leggibile.") from e
        csv_dentro = [n for n in archivio.namelist() if n.lower().endswith(".csv")]
        if not csv_dentro:
            raise ErroreImport("Nello ZIP non c'è un file CSV.")
        dati = archivio.open(csv_dentro[0])
    elif not nome.lower().endswith(".csv"):
        raise ErroreImport("Carica il file CSV o lo ZIP scaricato dal portale ANAC.")
    inizio = dati.read(256 * 1024)
    if not inizio.strip():
        raise ErroreImport("Il file è vuoto.")
    righe_intere = inizio[: inizio.rfind(b"\n") + 1] or inizio
    try:
        righe_intere.decode("utf-8")
        codifica = "utf-8-sig"
    except UnicodeDecodeError:
        codifica = "latin-1"
    prima_riga = righe_intere.split(b"\n", 1)[0].decode(codifica, errors="replace")
    separatore = max((";", ",", "|", "\t"), key=prima_riga.count)
    testo = io.TextIOWrapper(
        io.BufferedReader(_Catena(inizio, dati)), encoding=codifica, errors="replace", newline=""
    )
    lettore = csv.reader(testo, delimiter=separatore)
    intestazione = [c.strip().lstrip("\ufeff").lstrip("#").lower() for c in next(lettore)]
    for valori in lettore:
        if valori:
            yield dict(zip(intestazione, (v.strip() for v in valori), strict=False))


def _importo_anac(testo: str) -> Any:
    """Gli importi ANAC usano il punto decimale (es. 2882628.25); si accetta anche 1.234,56."""
    testo = (testo or "").strip()
    if not testo:
        return None
    if "," in testo:
        return importo_italiano(testo)
    try:
        from decimal import Decimal

        return Decimal(testo)
    except ArithmeticError:
        return None
    except ValueError:
        return None


def _data(s: str | None) -> date | None:
    s = (s or "").strip()[:19]
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(
                s if "T" in formato or " " in formato else s[:10], formato
            ).date()
        except ValueError:
            continue
    return None


def _primo(riga: dict[str, str], *nomi: str) -> str:
    for n in nomi:
        if riga.get(n):
            return riga[n]
    return ""


def tipo_file(intestazione: list[str]) -> str:
    if "data_scadenza_offerta" in intestazione or "oggetto_gara" in intestazione:
        return "cig"
    if any("categoria" in c for c in intestazione):
        return "categorie"
    raise ErroreImport(
        "Non riconosco il file: servono il dataset «CIG» (con oggetto_gara e "
        "data_scadenza_offerta) o il dataset «categorie-opera»."
    )


def importa(db: Session, sorgente: IO[bytes], nome: str, oggi: date | None = None) -> EsitoImport:
    oggi = oggi or date.today()
    righe = _righe(sorgente, nome)
    try:
        prima = next(righe)
    except StopIteration as e:
        raise ErroreImport("Il file non contiene righe.") from e
    tipo = tipo_file(list(prima))
    esito = EsitoImport(tipo=tipo)

    def tutte() -> Iterator[dict[str, str]]:
        yield prima
        yield from righe

    if tipo == "cig":
        _importa_cig(db, tutte(), esito, oggi)
    else:
        _importa_categorie(db, tutte(), esito)
    db.commit()
    return esito


def _importa_cig(
    db: Session, righe: Iterator[dict[str, str]], esito: EsitoImport, oggi: date
) -> None:
    per_cig = {a.cig: a for a in db.scalars(select(Appalto).where(Appalto.cig.is_not(None)))}
    for r in righe:
        esito.lette += 1
        cig = (r.get("cig") or "").upper()
        scadenza = _data(r.get("data_scadenza_offerta"))
        if not cig or scadenza is None or scadenza < oggi:
            esito.scartate += 1
            continue
        oggetto = _primo(r, "oggetto_lotto", "oggetto_gara") or cig
        if (
            r.get("oggetto_gara")
            and r.get("oggetto_lotto")
            and r["oggetto_gara"] != r["oggetto_lotto"]
        ):
            descrizione = f"{r['oggetto_gara']}\nLotto: {r['oggetto_lotto']}"
        else:
            descrizione = r.get("oggetto_gara") or None
        cpv = re.sub(r"\D", "", r.get("cod_cpv") or "")[:8]
        regione = regione_da_testo(
            re.sub(r"(?i)sezione\s+regionale", "", r.get("sezione_regionale") or "").strip()
        )
        provincia = PROVINCE.get((r.get("provincia") or "").strip().lower())
        dati: dict[str, Any] = {
            "titolo": oggetto,
            "stazione_appaltante": _primo(
                r, "denominazione_amministrazione_appaltante", "denominazione_centro_costo"
            )
            or None,
            "citta": None,
            "regioni": [regione] if regione else None,
            "province": [provincia] if provincia else None,
            "cpv": [cpv] if cpv else None,
            "divisioni_cpv": [cpv[:2]] if cpv[:2] in DIVISIONI_CPV else None,
            "natura": [NATURA[n]]
            if (n := (r.get("oggetto_principale_contratto") or "").upper()) in NATURA
            else None,
            "scadenza": scadenza,
            "pubblicazione": _data(r.get("data_pubblicazione")),
            "importo_stimato": _importo_anac(
                _primo(r, "importo_lotto", "importo_complessivo_gara")
            ),
            "cig": cig,
            "tipo_procedura": (r.get("tipo_scelta_contraente") or "").capitalize() or None,
            "descrizione": descrizione,
            "luogo_esecuzione": r.get("provincia") or None,
        }
        a = per_cig.get(cig)
        if a is None:
            a = Appalto(
                fonte="anac",
                id_esterno=f"anac-{cig}",
                link=None,
                raw={k: v for k, v in r.items() if v},
                **dati,
            )
            db.add(a)
            per_cig[cig] = a
            esito.importate += 1
        elif a.fonte == "anac":
            for k, v in dati.items():
                setattr(a, k, v)
            a.raw = {k: v for k, v in r.items() if v}
            esito.aggiornate += 1
        else:
            # Gara già presente da TED: si completano solo i campi mancanti
            for k, v in dati.items():
                if v is not None and getattr(a, k) in (None, []):
                    setattr(a, k, v)
            esito.aggiornate += 1


def _tipo_categoria(testo: str) -> str | None:
    t = (testo or "").upper()
    if "PREVAL" in t or t == "P":
        return "prevalente"
    if "SCORP" in t or t == "S" or "SECONDAR" in t:
        return "scorporabile"
    return None


def _importa_categorie(db: Session, righe: Iterator[dict[str, str]], esito: EsitoImport) -> None:
    per_cig = {a.cig: a for a in db.scalars(select(Appalto).where(Appalto.cig.is_not(None)))}
    nuove: dict[str, list[dict[str, Any]]] = {}
    for r in righe:
        esito.lette += 1
        cig = (r.get("cig") or "").upper()
        if cig not in per_cig:
            esito.scartate += 1
            continue
        codice_grezzo = _primo(r, "id_categoria", "cod_categoria", "categoria").upper()
        codice = re.sub(r"\s", "", codice_grezzo)
        m = re.fullmatch(r"(O[GS])(\d{1,2})(?:-?([AB]))?", codice)
        if m:
            codice = f"{m.group(1)}{int(m.group(2))}" + (f"-{m.group(3)}" if m.group(3) else "")
        if not codice:
            continue
        tipo = _tipo_categoria(
            _primo(r, "descrizione_tipo_categoria", "tipo_categoria", "cod_tipo_categoria")
        )
        nuove.setdefault(cig, []).append(
            {
                "codice": codice,
                "descrizione": CATEGORIE_SOA.get(codice)
                or _primo(r, "descrizione", "descrizione_categoria")
                or None,
                "tipo": tipo,
                "importo": None,
                "provenienza": "anac",
            }
        )
    for cig, categorie in nuove.items():
        a = per_cig[cig]
        a.categorie_soa = unisci_soa(categorie, a.categorie_soa)
        a.codici_soa = sorted({c["codice"] for c in a.categorie_soa or []}) or None
        esito.aggiornate += 1
