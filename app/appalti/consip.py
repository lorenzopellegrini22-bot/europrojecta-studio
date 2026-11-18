"""Gare e abilitazioni Consip (MEPA, SDAPA, convenzioni, accordi quadro) dagli open data ufficiali.

Verifica del 18/11/2026:
- dataset "Bandi e gare" su dati.consip.it, licenza CC BY 4.0, aggiornamento mensile, file
  `https://dati.consip.it/download/dataset/bandiegare<anno>.csv` (UTF-8, separatore virgola);
- robots.txt: vietata /api/ ai programmi, permessi i file in /download/; Crawl-Delay 10 s:
  qui si scarica UN file a notte, solo se Last-Modified è cambiato;
- tracciato ufficiale (tracciato-record-bandi-e-gare.xlsx): per i bandi MePA e SDA "Data
  Termine" è la scadenza del bando di abilitazione (vuota = sempre aperto); per convenzioni e
  accordi quadro è la fine del periodo di acquisto, non la scadenza delle offerte.
Si tengono quindi:
- MePA/SDA con Data Termine vuota o futura → "abilitazione aperta";
- convenzioni e accordi quadro pubblicati negli ultimi 12 mesi e non ancora aggiudicati →
  "gara in corso" (la scadenza delle offerte non è nel dataset: va letta sul bando).
Le singole RdO del MePA non sono pubbliche (sono procedure a invito): Consip ne pubblica solo
dati aggregati.
"""

import csv
import hashlib
import io
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.appalti import Appalto
from app.sources.http import USER_AGENT

URL = "https://dati.consip.it/download/dataset/bandiegare{anno}.csv"
ATTRIBUZIONE = "Dati: Consip S.p.A. (dati.consip.it), licenza CC BY 4.0."
PORTALE = "https://www.acquistinretepa.it/"


def _data(s: str | None) -> date | None:
    try:
        return datetime.strptime((s or "").strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def _importo(s: str | None) -> Decimal | None:
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d > 0 else None


def interpreta(riga: dict[str, str], oggi: date) -> dict[str, Any] | None:
    r = {k.lstrip("#").strip(): (v or "").strip() for k, v in riga.items() if k}
    strumento = r.get("Tipo_Strumento", "")
    termine = _data(r.get("Data_Termine"))
    pubblicazione = _data(r.get("Data_Pubblicazione"))
    if strumento in ("MePA", "SDA"):
        if termine is not None and termine < oggi:
            return None
        stato = "Abilitazione aperta"
        scadenza = termine
    else:
        aggiudicata = bool(r.get("Data_Aggiudicazione"))
        if aggiudicata or pubblicazione is None or pubblicazione < oggi - timedelta(days=365):
            return None
        stato = "Gara in corso (non ancora aggiudicata)"
        scadenza = None
    bando = r.get("Denominazione_Bando", "")
    lotto = r.get("Denominazione_Lotto", "")
    cig = r.get("Identificativo_Lotto", "")
    cig = cig if len(cig) == 10 and cig.isalnum() else None
    chiave = cig or hashlib.sha1(f"{bando}|{lotto}|{strumento}".encode()).hexdigest()[:16]
    nomi_strumento = {
        "MePA": "MePA – bando di abilitazione",
        "SDA": "Sistema dinamico di acquisizione (SDAPA)",
        "Convenzione": "Convenzione",
        "Accordo Quadro": "Accordo quadro",
    }
    procedura = nomi_strumento.get(strumento, strumento)
    if r.get("Tipo_Procedura"):
        procedura += f" – procedura {r['Tipo_Procedura']}"
    descrizione = [f"Stato: {stato}."]
    if r.get("Categoria_Merceologica") and r["Categoria_Merceologica"] != "NA":
        descrizione.append(f"Categoria merceologica: {r['Categoria_Merceologica']}.")
    if r.get("Tipologia_Lotto"):
        descrizione.append(f"Tipologia del lotto: {r['Tipologia_Lotto']}.")
    abilitati = r.get("Numero_Operatori_Economici_Aggiudicatari/Abilitati")
    if abilitati:
        descrizione.append(f"Operatori aggiudicatari/abilitati: {abilitati}.")
    if r.get("Verde") == "S":
        descrizione.append("Iniziativa con criteri ambientali (verde).")
    titolo = f"{bando} – {lotto}" if lotto and lotto != bando else bando
    return {
        "fonte": "consip",
        "id_esterno": f"consip-{chiave}",
        "titolo": titolo or "Iniziativa Consip",
        "stazione_appaltante": "Consip S.p.A.",
        "citta": "Roma",
        "natura": None,
        "scadenza": scadenza,
        "pubblicazione": pubblicazione,
        "importo_stimato": _importo(r.get("Base_Asta")),
        "link": PORTALE,
        "cig": cig,
        "tipo_procedura": procedura,
        "criterio_aggiudicazione": r.get("Criterio_Aggiudicazione") or None,
        "descrizione": " ".join(descrizione),
        "luogo_esecuzione": "Tutta Italia (iniziativa nazionale)",
        "ente_sito": PORTALE,
        "raw": r,
    }


def raccogli(
    db: Session,
    client: httpx.Client | None = None,
    oggi: date | None = None,
    ultima_versione: str | None = None,
) -> tuple[str, str | None]:
    """Scarica il file dell'anno corrente se cambiato. Restituisce (riepilogo, versione)."""
    oggi = oggi or date.today()
    url = URL.format(anno=oggi.year)
    proprio = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0))
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT})
    finally:
        if proprio:
            client.close()
    if r.status_code != 200:
        return f"Consip: file {oggi.year} non disponibile ({r.status_code}).", ultima_versione
    versione = r.headers.get("last-modified")
    testo = r.content.decode("utf-8-sig", errors="replace")
    righe = list(csv.DictReader(io.StringIO(testo)))
    if not righe or "Tipo_Strumento" not in righe[0]:
        raise ValueError("Consip: formato del file cambiato (manca Tipo_Strumento)")
    dati = [d for d in (interpreta(x, oggi) for x in righe) if d]
    tenuti = {d["id_esterno"] for d in dati}
    esistenti = {
        a.id_esterno: a for a in db.scalars(select(Appalto).where(Appalto.fonte == "consip"))
    }
    nuovi = 0
    for d in dati:
        a = esistenti.get(d["id_esterno"])
        if a is None:
            nuovo = Appalto(**d)
            db.add(nuovo)
            esistenti[d["id_esterno"]] = nuovo
            nuovi += 1
        else:
            for k, v in d.items():
                setattr(a, k, v)
    vecchi = set(esistenti) - tenuti
    if vecchi:
        db.execute(delete(Appalto).where(Appalto.id_esterno.in_(vecchi)))
    db.commit()
    return (
        f"Consip: {len(dati)} iniziative aperte ({nuovi} nuove, {len(vecchi)} tolte).",
        versione,
    )
