# ruff: noqa: E501
"""Import dei clienti da CSV (export del CRM precedente), brief sezione 6.

1. caricamento con rilevamento di codifica (UTF-8 o Windows-1252) e separatore;
2. mappatura colonna → campo, salvabile e riutilizzabile;
3. anteprima con gli errori evidenziati;
4. prova a vuoto, poi import vero;
5. report degli errori in CSV;
6. upsert per partita IVA: reimportare lo stesso file non crea duplicati;
7. i referenti possono stare nella riga dell'azienda o in un file a parte (tipo «referenti»).

Le funzioni di lettura e validazione sono pure; `importa` scrive nel database.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Azienda, AziendaAteco, Referente, Sede
from app.models.crm import STATI_CLIENTE
from app.models.crm_esteso import Tag
from app.pratiche.servizio import partita_iva_valida
from app.utils.numeri import importo_italiano
from app.utils.ricerca import normalizza
from app.utils.territorio import REGIONI

MAX_RIGHE = 5000
MAX_BYTE = 10 * 1024 * 1024

# campo → (etichetta, sinonimi nell'intestazione per la mappatura automatica)
CAMPI: dict[str, tuple[str, tuple[str, ...]]] = {
    "ragione_sociale": ("Ragione sociale", ("ragione sociale", "denominazione", "azienda", "nome azienda", "cliente", "societa")),
    "partita_iva": ("Partita IVA", ("partita iva", "p.iva", "piva", "p iva", "vat")),
    "codice_fiscale": ("Codice fiscale", ("codice fiscale", "cf", "c.f.")),
    "forma_giuridica": ("Forma giuridica", ("forma giuridica", "natura giuridica")),
    "email": ("Email azienda", ("email", "e-mail", "mail")),
    "pec": ("PEC", ("pec",)),
    "telefono": ("Telefono azienda", ("telefono", "tel", "cellulare")),
    "indirizzo": ("Indirizzo sede legale", ("indirizzo", "via", "sede legale")),
    "cap": ("CAP", ("cap",)),
    "comune": ("Comune", ("comune", "citta", "localita")),
    "provincia": ("Provincia (sigla)", ("provincia", "prov", "pr")),
    "regione": ("Regione", ("regione",)),
    "ateco": ("Codice ATECO", ("ateco", "codice ateco", "attivita ateco")),
    "ateco_descrizione": ("Descrizione ATECO", ("descrizione ateco", "attivita")),
    "dipendenti": ("Dipendenti", ("dipendenti", "addetti", "numero dipendenti")),
    "fatturato": ("Fatturato", ("fatturato", "ricavi", "volume d'affari")),
    "totale_attivo": ("Totale di bilancio", ("totale attivo", "totale bilancio", "totale di bilancio")),
    "anno_dati": ("Anno dei dati", ("anno", "anno dati", "anno bilancio")),
    "data_costituzione": ("Data di costituzione", ("data costituzione", "costituzione", "data di costituzione", "data inizio attivita")),
    "stato_cliente": ("Stato cliente (prospect/attivo/ex)", ("stato", "stato cliente")),
    "note": ("Note", ("note", "annotazioni")),
    "tag": ("Tag (separati da virgola)", ("tag", "etichette", "categoria")),
    "id_crm_origine": ("ID nel CRM precedente", ("id", "codice cliente", "id cliente")),
    "referente_nome": ("Referente: nome", ("referente", "nome referente", "nome")),
    "referente_cognome": ("Referente: cognome", ("cognome", "cognome referente")),
    "referente_ruolo": ("Referente: ruolo", ("ruolo", "qualifica")),
    "referente_email": ("Referente: email", ("email referente", "mail referente")),
    "referente_telefono": ("Referente: telefono", ("telefono referente", "cellulare referente")),
}  # fmt: skip
CAMPI_REFERENTE = tuple(c for c in CAMPI if c.startswith("referente_"))
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErroreImport(Exception):
    pass


def decodifica(dati: bytes) -> tuple[str, str]:
    """(testo, codifica). UTF-8 (anche con BOM) oppure Windows-1252 (Excel italiano)."""
    if len(dati) > MAX_BYTE:
        raise ErroreImport("File troppo grande (massimo 10 MB).")
    for codifica in ("utf-8-sig", "cp1252"):
        try:
            return dati.decode(codifica), "utf-8" if codifica == "utf-8-sig" else codifica
        except UnicodeDecodeError:
            continue
    raise ErroreImport("Codifica del file non riconosciuta (usa UTF-8 o Windows-1252).")


def separatore(testo: str) -> str:
    prima = testo.splitlines()[0] if testo else ""
    try:
        return csv.Sniffer().sniff(prima, delimiters=";,\t|").delimiter
    except csv.Error:
        return max(";,\t|", key=prima.count)


def leggi(testo: str, sep: str) -> tuple[list[str], list[list[str]]]:
    righe = list(csv.reader(io.StringIO(testo), delimiter=sep))
    righe = [r for r in righe if any(c.strip() for c in r)]
    if not righe:
        raise ErroreImport("Il file è vuoto.")
    intestazioni = [h.strip() for h in righe[0]]
    if len(righe) - 1 > MAX_RIGHE:
        raise ErroreImport(f"Troppe righe (massimo {MAX_RIGHE}).")
    return intestazioni, righe[1:]


def indovina_mappatura(intestazioni: list[str]) -> dict[str, str]:
    """colonna → campo, confrontando l'intestazione con i sinonimi (prima quelli esatti)."""
    mappatura: dict[str, str] = {}
    usati: set[str] = set()
    norm = {h: normalizza(h).replace("_", " ").strip() for h in intestazioni}
    for esatto in (True, False):
        for h in intestazioni:
            if h in mappatura:
                continue
            for campo, (_, sinonimi) in CAMPI.items():
                if campo in usati:
                    continue
                if (esatto and norm[h] in sinonimi) or (
                    not esatto and any(s in norm[h] for s in sinonimi if len(s) > 3)
                ):
                    mappatura[h] = campo
                    usati.add(campo)
                    break
    return mappatura


def _data(v: str) -> date | None:
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v, formato).date()
        except ValueError:
            continue
    raise ValueError


def _regione(v: str) -> str | None:
    n = normalizza(v)
    return next(
        (r for r in REGIONI if normalizza(r.split("/")[0]) == n or normalizza(r) == n), None
    )


@dataclass
class Riga:
    numero: int
    valori: dict[str, Any] = field(default_factory=dict)
    errori: list[str] = field(default_factory=list)


def valida(numero: int, riga: list[str], intestazioni: list[str], mappa: dict[str, str]) -> Riga:
    """Valori puliti e errori di una riga, secondo la mappatura colonna → campo."""
    r = Riga(numero=numero)
    grezzi = {
        mappa[h]: (riga[i].strip() if i < len(riga) else "")
        for i, h in enumerate(intestazioni)
        if mappa.get(h) in CAMPI
    }
    for campo, v in grezzi.items():
        if not v:
            continue
        try:
            if campo == "partita_iva":
                v = v.replace(" ", "").upper().removeprefix("IT")
                if not partita_iva_valida(v):
                    raise ValueError("partita IVA non valida")
            elif campo == "codice_fiscale":
                v = v.replace(" ", "").upper()
                if len(v) not in (11, 16):
                    raise ValueError("codice fiscale non valido")
            elif campo in ("dipendenti", "anno_dati"):
                v = int(v.replace(".", "").replace(" ", ""))
                if v < 0:
                    raise ValueError
            elif campo in ("fatturato", "totale_attivo"):
                n = importo_italiano(v.replace("€", "").strip())
                if n is None or n < 0:
                    raise ValueError
                v = n
            elif campo == "data_costituzione":
                v = _data(v)
            elif campo == "provincia":
                v = v.upper()
                if not (len(v) == 2 and v.isalpha()):
                    raise ValueError("provincia: usa la sigla di 2 lettere")
            elif campo == "cap":
                if not (v.isdigit() and len(v) == 5):
                    raise ValueError("CAP non valido")
            elif campo == "regione":
                reg = _regione(v)
                if reg is None:
                    raise ValueError(f"regione «{v}» non riconosciuta")
                v = reg
            elif campo == "stato_cliente":
                v = v.lower()
                if v not in STATI_CLIENTE:
                    raise ValueError("stato: prospect, attivo o ex")
            elif campo in ("email", "pec", "referente_email"):
                if not _EMAIL.match(v):
                    raise ValueError(f"{CAMPI[campo][0]}: indirizzo non valido")
            elif campo == "ateco":
                v = v.replace(" ", "")
                if not re.match(r"^\d{2}(\.\d{1,2}){0,2}$", v):
                    raise ValueError("ATECO nel formato 62.01")
        except ValueError as e:
            r.errori.append(str(e) if str(e) else f"{CAMPI[campo][0]} non valido")
            continue
        r.valori[campo] = v
    return r


def valida_azienda(r: Riga) -> None:
    if not r.valori.get("partita_iva"):
        r.errori.append("manca la partita IVA (serve a riconoscere il cliente)")
    if not r.valori.get("ragione_sociale"):
        r.errori.append("manca la ragione sociale")


def valida_referente(r: Riga) -> None:
    if not r.valori.get("partita_iva"):
        r.errori.append("manca la partita IVA dell'azienda")
    if not r.valori.get("referente_nome"):
        r.errori.append("manca il nome del referente")


@dataclass
class Esito:
    totali: int = 0
    importate: int = 0
    nuove: int = 0
    aggiornate: int = 0
    scartate: int = 0
    errori: list[dict[str, Any]] = field(default_factory=list)


def _referente(a: Azienda, v: dict[str, Any]) -> None:
    nome = v.get("referente_nome")
    if not nome:
        return
    esistente = next(
        (
            x
            for x in a.referenti
            if (v.get("referente_email") and x.email == v.get("referente_email"))
            or (x.nome == nome and (x.cognome or None) == v.get("referente_cognome"))
        ),
        None,
    )
    if esistente is None:
        esistente = Referente(nome=nome, principale=not a.referenti)
        a.referenti.append(esistente)
    for campo in ("cognome", "ruolo", "email", "telefono"):
        if v.get(f"referente_{campo}"):
            setattr(esistente, campo, v[f"referente_{campo}"])


def importa(
    db: Session, testo_csv: str, sep: str, mappa: dict[str, str], tipo: str, prova: bool
) -> Esito:
    """Prova a vuoto (prova=True: nessuna scrittura) o import vero. Upsert per partita IVA."""
    intestazioni, righe = leggi(testo_csv, sep)
    esito = Esito(totali=len(righe))
    viste: set[str] = set()
    for n, riga in enumerate(righe, start=2):  # riga 1 = intestazioni
        r = valida(n, riga, intestazioni, mappa)
        (valida_referente if tipo == "referenti" else valida_azienda)(r)
        if r.errori:
            esito.scartate += 1
            esito.errori.append({"riga": n, "errori": r.errori})
            continue
        v = r.valori
        piva = v["partita_iva"]
        a = db.scalar(select(Azienda).where(Azienda.partita_iva == piva))
        if tipo == "referenti":
            if a is None and piva not in viste:
                esito.scartate += 1
                esito.errori.append(
                    {"riga": n, "errori": ["azienda non trovata: importa prima le aziende"]}
                )
                continue
            if not prova and a is not None:
                _referente(a, v)
            esito.importate += 1
            continue
        if piva in viste:
            # stessa azienda ripetuta nel file (es. una riga per ogni referente)
            esito.importate += 1
            if not prova and a is not None:
                _referente(a, v)
            continue
        if a is None:
            esito.nuove += 1
            if prova:
                viste.add(piva)
                esito.importate += 1
                continue
            a = Azienda(ragione_sociale=v["ragione_sociale"], partita_iva=piva)
            db.add(a)
        else:
            esito.aggiornate += 1
        viste.add(piva)
        esito.importate += 1
        if prova:
            continue
        _applica(db, a, v)
        db.flush()
    if not prova:
        db.flush()
    return esito


def _applica(db: Session, a: Azienda, v: dict[str, Any]) -> None:
    """Scrive i valori presenti nel file; i campi vuoti non cancellano quelli esistenti."""
    for campo in (
        "ragione_sociale", "codice_fiscale", "forma_giuridica", "email", "pec", "telefono",
        "dipendenti", "fatturato", "totale_attivo", "anno_dati", "data_costituzione",
        "stato_cliente", "id_crm_origine",
    ):  # fmt: skip
        if campo in v:
            setattr(a, campo, v[campo])
    if v.get("note"):
        a.note = v["note"] if not a.note or v["note"] in a.note else f"{a.note}\n{v['note']}"
    if any(k in v for k in ("indirizzo", "cap", "comune", "provincia", "regione")):
        sede = a.sede_legale
        if sede is None:
            sede = Sede(tipo="legale")
            a.sedi.append(sede)
        for campo, attr in (
            ("indirizzo", "indirizzo"),
            ("cap", "cap"),
            ("comune", "comune_testo"),
            ("provincia", "provincia"),
            ("regione", "regione"),
        ):
            if campo in v:
                setattr(sede, attr, v[campo])
    if v.get("ateco"):
        codice = v["ateco"]
        for x in a.ateco:
            x.primario = False
        esistente = next((x for x in a.ateco if x.codice == codice), None)
        if esistente is None:
            a.ateco.append(
                AziendaAteco(
                    codice=codice,
                    descrizione=v.get("ateco_descrizione"),
                    versione="2007",
                    primario=True,
                )
            )
        else:
            esistente.primario = True
    if v.get("tag"):
        for nome in [" ".join(t.split())[:60] for t in str(v["tag"]).split(",") if t.strip()]:
            t = db.scalar(select(Tag).where(func.lower(Tag.nome) == nome.lower()))
            if t is None:
                t = Tag(nome=nome)
                db.add(t)
            if t not in a.tag:
                a.tag.append(t)
    _referente(a, v)


def csv_errori(errori: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";")
    w.writerow(["Riga", "Errori"])
    for e in errori:
        w.writerow([e["riga"], "; ".join(e["errori"])])
    return "﻿" + buffer.getvalue()


def anteprima(
    testo_csv: str, sep: str, mappa: dict[str, str], tipo: str, quante: int = 10
) -> tuple[list[str], list[tuple[list[str], Riga]]]:
    intestazioni, righe = leggi(testo_csv, sep)
    out = []
    for n, riga in enumerate(righe[:quante], start=2):
        r = valida(n, riga, intestazioni, mappa)
        (valida_referente if tipo == "referenti" else valida_azienda)(r)
        out.append((riga, r))
    return intestazioni, out


def totale_decimale(v: Any) -> Decimal | None:  # usato nei template
    return v if isinstance(v, Decimal) else None
