"""Fonte europea: bandi della Commissione europea dal portale Funding & Tenders (verifica del
23/11/2026, dettagli in docs/fonti/ue.md).

Come si raccoglie:
- l'API di ricerca che usa il portale stesso (api.tech.ec.europa.eu/search-api), in POST, con
  i filtri «topic» (type 1: inviti a presentare proposte) e stato aperto o in arrivo. Si
  escludono le gare EuropeAid (type 2, per Paesi extra UE) e i bandi «a cascata» (type 8,
  spesso riservati a un solo Paese);
- l'API non ha robots.txt (404) né condizioni proprie pubblicate; vale la nota legale della
  Commissione: riutilizzo consentito citando la fonte e indicando le modifiche;
- circa 6 richieste a notte, una al secondo.

Il portale non dice chi può partecipare in forma strutturata: i beneficiari si ricavano dal
programma e dal titolo (vedi tipologie). Testi in inglese.
"""

import contextlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from app.sources import http
from app.sources.base import RecordBando, RequisitiFonte
from app.sources.regioni import comune
from app.sources.regioni.comune import ErroreFormato

CODICE = "ue_funding_tenders"
NOME = "Commissione europea (Funding & Tenders)"
URL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home"
API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
ATTRIBUZIONE = (
    "Dati: Commissione europea, portale Funding & Tenders (riutilizzo consentito citando la "
    "fonte; dati rielaborati). Fa fede il testo ufficiale del bando sul portale."
)
PER_PAGINA = 100
MAX_PAGINE = 15
APERTO, IN_ARRIVO = "31094502", "31094501"
PREFISSO_ID = "ue-"

# Programmi (dal prefisso del codice del topic)
PROGRAMMI = {
    "HORIZON": "Horizon Europe", "ERC": "Horizon Europe (ERC)", "CREA": "Europa Creativa",
    "DIGITAL": "Europa digitale", "CERV": "Cittadini, uguaglianza, diritti e valori (CERV)",
    "LIFE": "LIFE (ambiente e clima)", "ERASMUS": "Erasmus+", "EU4H": "EU4Health",
    "SMP": "Mercato unico (SMP)", "CEF": "Meccanismo per collegare l'Europa (CEF)",
    "EMFAF": "Fondo europeo per gli affari marittimi (FEAMPA)",
    "EDF": "Fondo europeo per la difesa",
    "JUST": "Giustizia", "ISF": "Fondo sicurezza interna", "AMIF": "Fondo asilo e migrazione",
    "EUBA": "Euratom e altri", "EURATOM": "Euratom", "UCPM": "Protezione civile dell'Unione",
    "AGRIP": "Promozione dei prodotti agricoli", "SOCPL": "Dialogo sociale", "ESF": "FSE+",
    "I3": "Investimenti interregionali in innovazione (I3)", "IMCAP": "Strumenti della PAC",
}  # fmt: skip
# Programmi di sola ricerca o difesa: non per imprese comuni né per enti locali
SOLO_RICERCA = ("ERC", "EURATOM", "EDF", "EUBA")
_SOLO_RICERCA_TITOLO = re.compile(
    r"\b(doctoral networks?|postdoctoral|msca|erasmus mundus|fellowships?|erc)\b", re.I
)
_ENTI = re.compile(
    r"\b(cities|city|municipalit\w*|towns?|local (and regional )?authorit\w*|regional "
    r"authorit\w*|public authorit\w*|public administrations?|neighbourhoods?|"
    r"local communities|remembrance|citizens'? engagement|town[- ]twinning)\b",
    re.I,
)
_IMPRESE = re.compile(
    r"\b(smes?|small and medium|companies|enterprises?|start-?ups?|scale-?ups?|industr\w*|"
    r"businesses?|entrepreneur\w*)\b",
    re.I,
)
_ONG = re.compile(r"\b(civil society organisations?|ngos?|intermediaries)\b", re.I)
PROGRAMMI_ENTI = ("CERV", "LIFE", "UCPM", "AMIF", "I3")
PROGRAMMI_IMPRESE = ("DIGITAL", "SMP", "EMFAF", "AGRIP", "I3", "CREA")


def _primo(md: dict[str, Any], chiave: str) -> Any:
    v = md.get(chiave)
    return v[0] if isinstance(v, list) and v else v


def _data(testo: str | None) -> date | None:
    if not testo:
        return None
    try:
        return datetime.fromisoformat(testo[:19]).date()
    except ValueError:
        return None


_BLOCCHI = ("p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "ul", "ol")


def html_in_testo(html: str) -> str:
    """Testo a paragrafi: a capo solo tra i blocchi (paragrafi, voci di elenco, titoli).
    testo_da_html andava a capo a ogni link o grassetto, spezzando le frasi del portale."""
    if not html:
        return ""
    s = comune.zuppa(html)
    for el in s(["script", "style", "sup"]):
        el.decompose()
    for el in s.find_all(_BLOCCHI):
        el.insert_before("\n")
        el.insert_after("\n")
    righe = (" ".join(r.split()) for r in s.get_text("").splitlines())
    testo = "\n".join(r for r in righe if r)
    return testo.replace("﻿", "")


def _testo(md: dict[str, Any]) -> str:
    html = " ".join(str(x) for x in md.get("descriptionByte") or [])
    return html_in_testo(html)[:6000]


def _condizioni(md: dict[str, Any]) -> str:
    """Condizioni del topic: ammissibilità, Paesi ammessi, criteri di valutazione e di
    assegnazione, forma del contributo (spesso rimandano agli allegati del programma)."""
    html = " ".join(str(x) for x in md.get("topicConditions") or [])
    return html_in_testo(html)[:4000]


def _euro(v: Decimal | None) -> str:
    return f"{int(v):,} €".replace(",", ".") if v else ""


def _importi(md: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Contributo minimo e massimo per progetto e dotazione del topic (budgetOverview)."""
    try:
        dati = json.loads(_primo(md, "budgetOverview") or "{}")
    except (TypeError, ValueError):
        return None, None, None
    minimi, massimi, totale = [], [], Decimal(0)
    for azioni in (dati.get("budgetTopicActionMap") or {}).values():
        for az in azioni:
            if az.get("minContribution"):
                minimi.append(Decimal(str(az["minContribution"])))
            if az.get("maxContribution"):
                massimi.append(Decimal(str(az["maxContribution"])))
            for v in (az.get("budgetYearMap") or {}).values():
                with contextlib.suppress(ArithmeticError):
                    totale += Decimal(str(v))
    return (min(minimi) if minimi else None, max(massimi) if massimi else None,
            totale or None)  # fmt: skip


def tipologie(identificativo: str, titolo: str, testo: str) -> list[str]:
    """Beneficiari ricavati dal programma e dal titolo (il portale non li dà strutturati).
    Il testo conta solo se ne parla più volte: i bandi di ricerca citano spesso gli enti
    locali come parti interessate, non come beneficiari (prova del 23/11/2026)."""
    programma = identificativo.split("-")[0].upper()
    if programma in SOLO_RICERCA or _SOLO_RICERCA_TITOLO.search(titolo):
        return ["Università/Ente di Ricerca"]
    out = []
    # CERV ha anche inviti per le sole organizzazioni della società civile
    solo_ong = bool(_ONG.search(titolo)) and not _ENTI.search(titolo)
    programma_enti = programma in PROGRAMMI_ENTI and not solo_ong
    if programma_enti or _ENTI.search(titolo) or len(_ENTI.findall(testo)) >= 3:
        out.append("Ente Pubblico")
    if (
        programma in PROGRAMMI_IMPRESE
        or _IMPRESE.search(titolo)
        or len(_IMPRESE.findall(testo)) >= 3
    ):
        out.append("Impresa")
    if programma in ("HORIZON", "DIGITAL", "ERASMUS", "EU4H"):
        out.append("Università/Ente di Ricerca")
    return out or ["Università/Ente di Ricerca"]


def record(r: dict[str, Any], oggi: date) -> RecordBando | None:
    md = r.get("metadata") or {}
    ident = _primo(md, "identifier")
    titolo = (_primo(md, "title") or "").strip()
    if titolo == ident and _primo(md, "callTitle"):
        titolo = _primo(md, "callTitle").strip()  # alcuni topic CERV hanno solo il codice
    if not ident or not titolo:
        return None
    scadenze = sorted(d for d in (_data(x) for x in md.get("deadlineDate") or []) if d)
    future = [d for d in scadenze if d >= oggi]
    if scadenze and not future:
        return None  # tutte le scadenze passate: il portale a volte lo lascia «aperto»
    apertura = _data(_primo(md, "startDate"))
    testo = _testo(md)
    programma = ident.split("-")[0].upper()
    minimo, massimo, dotazione = _importi(md)
    req = RequisitiFonte(
        territorio="nazionale",
        tipologie_soggetto=tipologie(ident, titolo, testo),
        importo_min=minimo,
        importo_max=massimo,
        dotazione=dotazione,
    )
    req.da_verificare = ["dimensioni", "ateco", "forma_agevolazione", "categorie_spesa"]
    tipo_azione = _primo(md, "typesOfAction")
    invito = _primo(md, "callTitle")
    condizioni = _condizioni(md)
    contributo = ""
    if minimo or massimo:
        contributo = "Contributo UE per progetto: " + (
            f"da {_euro(minimo)} a {_euro(massimo)}"
            if minimo and massimo and minimo != massimo
            else _euro(massimo or minimo)
        )
    parti = [
        f"Programma: {PROGRAMMI.get(programma, programma)}",
        f"Invito: {invito}" if invito else "",
        f"Tipo di azione: {tipo_azione}" if tipo_azione else "",
        contributo,
        f"Dotazione del topic: {_euro(dotazione)}" if dotazione else "",
        "Testo originale in inglese sul portale Funding & Tenders.",
    ]
    descrizione = "\n".join(x for x in parti if x) + "\n\n" + testo
    if condizioni:
        descrizione += "\n\nCONDIZIONI E CRITERI DI VALUTAZIONE (dal portale)\n" + condizioni
    stato = "in_arrivo" if (str(_primo(md, "status")) == IN_ARRIVO or
                            (apertura and apertura > oggi)) else "aperto"  # fmt: skip
    return RecordBando(
        id_esterno=f"{PREFISSO_ID}{ident}"[:100],
        titolo=titolo[:500],
        raw={
            "identifier": ident,
            "callTitle": invito,
            "programma": programma,
            "typesOfAction": tipo_azione,
            "scadenze": [d.isoformat() for d in scadenze],
            "url": r.get("url"),
        },  # fmt: skip
        ente=f"Commissione europea – {PROGRAMMI.get(programma, programma)}",
        descrizione=descrizione[:12000],
        link_ufficiale=r.get("url"),
        data_apertura=apertura,
        data_chiusura=future[0] if future else None,
        note_apertura_chiusura=(
            f"Più scadenze: {', '.join(d.strftime('%d/%m/%Y') for d in future)}."
            if len(future) > 1
            else None
        ),  # fmt: skip
        stato_fonte=stato,
        requisiti=req,
    )


def _richiesta(client: httpx.Client, pagina: int) -> dict[str, Any]:
    filtro = {"bool": {"must": [
        {"terms": {"type": ["1"]}}, {"terms": {"status": [APERTO, IN_ARRIVO]}},
    ]}}  # fmt: skip
    parti = {
        "query": (None, json.dumps(filtro), "application/json"),
        "languages": (None, json.dumps(["en"]), "application/json"),
        "sort": (None, json.dumps({"field": "sortStatus", "order": "ASC"}), "application/json"),
    }
    params = {"apiKey": "SEDIA", "text": "***", "pageSize": str(PER_PAGINA),
              "pageNumber": str(pagina)}  # fmt: skip
    ultimo: Exception | None = None
    for _ in range(http.TENTATIVI_PAGINA):
        http._attendi_turno(API)
        try:
            risposta = client.post(API, params=params, files=parti)
        except httpx.TransportError as e:
            ultimo = e
            continue
        if risposta.status_code >= 500:
            ultimo = httpx.HTTPStatusError("5xx", request=risposta.request, response=risposta)
            continue
        risposta.raise_for_status()
        return risposta.json()
    raise http.ErroreDownload(f"Funding & Tenders: {ultimo}")


def raccogli(client: httpx.Client) -> list[dict[str, Any]]:
    risultati: list[dict[str, Any]] = []
    for n in range(1, MAX_PAGINE + 1):
        pagina = _richiesta(client, n).get("results") or []
        risultati += pagina
        if len(pagina) < PER_PAGINA:
            break
    if not risultati:
        raise ErroreFormato("Funding & Tenders: nessun bando nella risposta")
    # si tiene solo il necessario (le descrizioni HTML complete pesano diversi MB)
    tenuti = []
    for r in risultati:
        md = r.get("metadata") or {}
        chiavi = ("identifier", "title", "callTitle", "status", "startDate", "deadlineDate",
                  "descriptionByte", "budgetOverview", "typesOfAction",
                  "topicConditions")  # fmt: skip
        tenuti.append({"url": r.get("url"), "metadata": {k: md.get(k) for k in chiavi}})
    return tenuti


class FundingTenders:
    codice = CODICE
    nome = NOME
    url = URL
    soglia_sanita = 0.5
    assenze_per_scomparsa = 2

    def versione_remota(self, client: httpx.Client) -> str | None:
        return None

    def scarica(self, client: httpx.Client) -> Path:
        return comune.salva_pacchetto({"risultati": raccogli(client)})

    def interpreta(self, percorso: Path, oggi: date | None = None) -> list[RecordBando]:
        oggi = oggi or datetime.now(UTC).date()
        visti: dict[str, RecordBando] = {}
        for r in comune.leggi_pacchetto(percorso)["risultati"]:
            rec = record(r, oggi)
            if rec is not None:
                visti.setdefault(rec.id_esterno, rec)  # lo stesso topic compare più volte
        return list(visti.values())


FONTE = FundingTenders()
