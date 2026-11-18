"""Raccolta degli appalti pubblici italiani da TED (Tenders Electronic Daily, Unione europea).

Verificato il 18/11/2026 sulla documentazione ufficiale (docs.ted.europa.eu) e sulla
specifica OpenAPI (api.ted.europa.eu/api-v3.yaml):
- POST https://api.ted.europa.eu/v3/notices/search, senza autenticazione;
- query "expert": paese dell'acquirente Italia, bandi di gara (cn-standard, cn-social,
  cn-desg) con termine per le offerte non ancora scaduto;
- modalità ITERATION: 250 avvisi per pagina e un token per la pagina successiva;
- limiti: 250 avvisi e 10.000 campi per pagina.

Copertura: TED pubblica gli appalti sopra le soglie europee (e quelli che le stazioni
appaltanti scelgono di pubblicare). Gli appalti sotto soglia sono nella Banca Dati Nazionale
dei Contratti Pubblici di ANAC, il cui portale non consente l'accesso automatico dal server.
"""

import logging
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appalti import Appalto
from app.sources.http import USER_AGENT
from app.utils.nuts_italia import NUTS2_REGIONE, NUTS3_PROVINCIA

log = logging.getLogger(__name__)

URL = "https://api.ted.europa.eu/v3/notices/search"
QUERY = (
    "buyer-country=ITA AND notice-type IN (cn-standard cn-social cn-desg) "
    "AND deadline-receipt-tender-date-lot>=today()"
)
CAMPI = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-city",
    "place-of-performance",
    "classification-cpv",
    "deadline-receipt-tender-date-lot",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "contract-nature",
    "publication-date",
    "links",
    # Scheda completa (nomi verificati sulla specifica api-v3.yaml)
    "procedure-type",
    "award-criterion-type-lot",
    "description-proc",
    "legal-basis",
    "organisation-email-buyer",
    "organisation-tel-buyer",
    "organisation-internet-address-buyer",
    "organisation-street-buyer",
    "organisation-post-code-buyer",
    "organisation-city-buyer",
    "document-url-lot",
    "submission-url-lot",
    "estimated-value-proc",
    "place-of-performance-city-lot",
]
PER_PAGINA = 250
MAX_PAGINE = 60
NATURA = {"works": "lavori", "services": "servizi", "supplies": "forniture"}
ATTRIBUZIONE = "Dati: TED – Tenders Electronic Daily (ted.europa.eu), © Unione europea."


class ErroreTed(Exception):
    pass


def _lingua(valore: Any) -> Any:
    """I campi multilingua sono dizionari lingua → valore: si preferisce l'italiano."""
    if isinstance(valore, dict):
        for lingua in ("ita", "mul", "eng"):
            if lingua in valore:
                return valore[lingua]
        return next(iter(valore.values()), None)
    return valore


def _primo(valore: Any) -> str | None:
    v = _lingua(valore)
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v else None


def _lista(valore: Any) -> list[str]:
    v = _lingua(valore)
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    visti: list[str] = []
    for x in v:
        s = str(x).strip()
        if s and s not in visti:
            visti.append(s)
    return visti


def _data(valore: str | None) -> date | None:
    """«2026-12-18+01:00» → 18/12/2026."""
    if not valore:
        return None
    try:
        return date.fromisoformat(valore[:10])
    except ValueError:
        return None


def _url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if u.startswith(("http://", "https://")):
        return u
    return f"https://{u}" if "." in u and " " not in u else None


def _dettagli(n: dict[str, Any], titolo: str) -> dict[str, Any]:
    """Campi della scheda: ente e contatti, procedura, criterio, descrizione, base giuridica,
    documenti, piattaforma di gara, CIG e categorie SOA citati nel testo."""
    from app.appalti.scheda import (
        BASI_GIURIDICHE,
        CRITERI,
        PROCEDURE,
        estrai_cig,
        estrai_soa,
        unisci_soa,
    )

    descrizione = _primo(n.get("description-proc"))
    via = _primo(n.get("organisation-street-buyer"))
    cap = _primo(n.get("organisation-post-code-buyer"))
    citta = _primo(n.get("organisation-city-buyer")) or _primo(n.get("buyer-city"))
    indirizzo = " ".join(x for x in (via, "-" if via and (cap or citta) else None, cap, citta) if x)
    criteri = [CRITERI.get(c, c) for c in _lista(n.get("award-criterion-type-lot"))]
    basi = [BASI_GIURIDICHE.get(b, b) for b in _lista(n.get("legal-basis"))]
    documenti = [
        {"titolo": "Documenti di gara", "url": u}
        for u in (_url(x) for x in _lista(n.get("document-url-lot")))
        if u
    ]
    procedura = _primo(n.get("procedure-type"))
    luoghi = _lista(n.get("place-of-performance-city-lot"))
    categorie = unisci_soa(estrai_soa(f"{titolo}\n{descrizione or ''}"))
    return {
        "cig": estrai_cig(titolo, descrizione),
        "tipo_procedura": PROCEDURE.get(procedura, procedura) if procedura else None,
        "criterio_aggiudicazione": "; ".join(dict.fromkeys(criteri)) or None,
        "descrizione": descrizione,
        "riferimenti_legge": "; ".join(dict.fromkeys(basi)) or None,
        "luogo_esecuzione": ", ".join(luoghi[:3]) or None,
        "ente_indirizzo": indirizzo or None,
        "ente_email": _primo(n.get("organisation-email-buyer")),
        "ente_telefono": _primo(n.get("organisation-tel-buyer")),
        "ente_sito": _url(_primo(n.get("organisation-internet-address-buyer"))),
        "link_partecipazione": _url(_primo(n.get("submission-url-lot"))),
        "documenti": documenti[:5] or None,
        "categorie_soa": categorie,
        "codici_soa": sorted({c["codice"] for c in categorie or []}) or None,
    }


def interpreta(n: dict[str, Any]) -> dict[str, Any] | None:
    numero = n.get("publication-number")
    if not numero:
        return None
    nuts = [c for c in _lista(n.get("place-of-performance")) if c.startswith("IT")]
    regioni = sorted({NUTS2_REGIONE[c[:4]] for c in nuts if c[:4] in NUTS2_REGIONE})
    province = sorted({NUTS3_PROVINCIA[c][0] for c in nuts if c in NUTS3_PROVINCIA})
    cpv = _lista(n.get("classification-cpv"))
    scadenze = [
        d for d in (_data(x) for x in _lista(n.get("deadline-receipt-tender-date-lot"))) if d
    ]
    importo: Decimal | None = None
    valute = _lista(n.get("estimated-value-cur-lot"))
    if not valute or valute == ["EUR"]:
        totale = Decimal(0)
        for x in n.get("estimated-value-lot") or []:
            try:
                totale += Decimal(str(x))
            except InvalidOperation:
                continue
        importo = totale if 0 < totale < Decimal("1e14") else None
    titolo = _primo(n.get("notice-title")) or numero
    # Il titolo TED inizia con «Italia – <categoria CPV> – »: si toglie il prefisso del paese
    for prefisso in ("Italia – ", "Italia - ", "Italy – "):
        if titolo.startswith(prefisso):
            titolo = titolo[len(prefisso) :]
    link = None
    html = (n.get("links") or {}).get("html") or {}
    link = html.get("ITA") or html.get("ENG")
    return {
        "id_esterno": numero,
        "titolo": titolo,
        "stazione_appaltante": _primo(n.get("buyer-name")),
        "citta": _primo(n.get("buyer-city")),
        "nuts": nuts or None,
        "regioni": regioni or None,
        "province": province or None,
        "cpv": cpv or None,
        "divisioni_cpv": sorted({c[:2] for c in cpv if c[:2].isdigit()}) or None,
        "natura": sorted({NATURA[x] for x in _lista(n.get("contract-nature")) if x in NATURA})
        or None,
        "scadenza": min(scadenze) if scadenze else None,
        "pubblicazione": _data(_primo(n.get("publication-date"))),
        "importo_stimato": importo,
        "link": link,
        "raw": {k: v for k, v in n.items() if k != "links"},
        **_dettagli(n, titolo),
    }


def scarica(client: httpx.Client, pausa: float = 1.0) -> list[dict[str, Any]]:
    avvisi: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(MAX_PAGINE):
        corpo: dict[str, Any] = {
            "query": QUERY,
            "fields": CAMPI,
            "limit": PER_PAGINA,
            "paginationMode": "ITERATION",
        }
        if token:
            corpo["iterationNextToken"] = token
        r = client.post(URL, json=corpo, headers={"User-Agent": USER_AGENT})
        if r.status_code >= 400:
            raise ErroreTed(f"TED ha risposto {r.status_code}: {r.text[:300]}")
        dati = r.json()
        pagina = dati.get("notices") or []
        avvisi.extend(pagina)
        token = dati.get("iterationNextToken")
        if not pagina or not token:
            break
        time.sleep(pausa)
    return avvisi


def raccogli(db: Session, client: httpx.Client | None = None, pausa: float = 1.0) -> str:
    """Scarica i bandi aperti e aggiorna la tabella appalto. Restituisce un riepilogo."""
    proprio = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0))
    try:
        grezzi = scarica(client, pausa)
    finally:
        if proprio:
            client.close()
    esistenti = {a.id_esterno: a for a in db.scalars(select(Appalto))}
    nuovi = aggiornati = 0
    ora = datetime.now(UTC)
    for n in grezzi:
        dati = interpreta(n)
        if dati is None:
            continue
        a = esistenti.get(dati["id_esterno"])
        if a is None:
            a = Appalto(fonte="ted", visto_il=ora, **dati)
            db.add(a)
            esistenti[a.id_esterno] = a
            nuovi += 1
        else:
            from app.appalti.scheda import unisci_soa

            categorie = unisci_soa(
                [c for c in a.categorie_soa or [] if c.get("provenienza") == "anac"],
                dati["categorie_soa"],
            )
            dati["categorie_soa"] = categorie
            dati["codici_soa"] = sorted({c["codice"] for c in categorie or []}) or None
            for k, v in dati.items():
                setattr(a, k, v)
            aggiornati += 1
    db.commit()
    return f"TED: {len(grezzi)} bandi aperti letti, {nuovi} nuovi, {aggiornati} aggiornati."
