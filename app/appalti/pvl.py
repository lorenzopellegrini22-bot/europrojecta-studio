"""Bandi di gara dalla Piattaforma di Pubblicità a Valore Legale di ANAC (verifica del 22/11/2026).

Perché: dal 1/1/2024 la pubblicità legale di tutti i bandi, anche sotto le soglie europee,
passa dalla Banca Dati Nazionale dei Contratti Pubblici di ANAC e si legge sulla piattaforma
pubblica pubblicitalegale.anticorruzione.it (senza credenziali). TED ha solo i bandi sopra
soglia e senza categorie SOA; i file open data ANAC vanno scaricati a mano dal browser.

Come si raccoglie (gli stessi indirizzi che usa la pagina pubblica «Bandi e avvisi di
indizione»; il sito non ha un robots.txt e risponde normalmente a un programma che si
presenta con il proprio nome):
- elenco: GET /api/v0/avvisi?dataPubblicazioneStart=gg/mm/aaaa&dataPubblicazioneEnd=…
  &codiceScheda=2,4&page=N&size=100 (2,4 = bandi e avvisi di indizione, come la pagina);
- dettaglio: GET /api/v0/avvisi/{idAvviso}, che ha il codice CPV («55510000_Servizi di
  mensa»; l'elenco ne ha solo la descrizione). Si chiede solo per le gare aperte nuove.
Ogni avviso ha committente, CIG e oggetto dei lotti, natura, CPV, valore stimato, provincia
e comune, scadenza, link ai documenti e, per i lavori, le categorie SOA («OG 1», «OS 30»).

Scelte:
- gli avvisi con il link a TED (sopra soglia) non si duplicano: si completa la gara TED con
  CIG e categorie SOA, che TED non ha; gli altri (sotto soglia) diventano gare «anac»;
- una gara per avviso (idAppalto), con tutti i lotti: le rettifiche aggiornano la stessa;
- si tengono solo le gare con la scadenza non ancora passata;
- ogni notte si rilegge l'elenco degli ultimi 60 giorni di pubblicazioni (circa 5.000 avvisi,
  due pagine al giorno: un paio di minuti) e si chiede il dettaglio solo degli avvisi non
  ancora salvati, al massimo MAX_DETTAGLI per notte: quelli rimasti si prendono la notte dopo.
  Fino al 22/11/2026 si rileggevano solo gli ultimi 3 giorni e le gare oltre il limite della
  prima notte non sarebbero più arrivate;
- pause di almeno un secondo tra le richieste (app/sources/http.py).
Dati: ANAC, Banca Dati Nazionale dei Contratti Pubblici (licenza CC BY-SA 4.0 degli open data
ANAC); fa fede l'avviso pubblicato sulla piattaforma.
"""

import html
import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appalti import Appalto
from app.utils.cpv import DIVISIONI_CPV
from app.utils.nuts_italia import NUTS3_PROVINCIA

log = logging.getLogger(__name__)

BASE = "https://pubblicitalegale.anticorruzione.it"
ELENCO = f"{BASE}/api/v0/avvisi"
SCHEDE_BANDI = "2,4"
PER_PAGINA = 100
MAX_PAGINE_PER_GIORNO = 20
GIORNI_ELENCO = 60
MAX_DETTAGLI = 2500  # per notte (una richiesta al secondo): il resto la notte dopo
PREFISSO = "pvl-"
FUSO = ZoneInfo("Europe/Rome")
NATURA = {"lavori": "lavori", "servizi": "servizi", "forniture": "forniture"}
ATTRIBUZIONE = (
    "Dati: ANAC, Banca Dati Nazionale dei Contratti Pubblici (Piattaforma di Pubblicità a "
    "Valore Legale). Fa fede l'avviso pubblicato."
)


class ErrorePvl(Exception):
    pass


def _norm(testo: str) -> str:
    from app.utils.ricerca import normalizza

    t = normalizza(testo).replace("'", " ")
    return " ".join(re.split(r"[/\-]", t)[0].split())


_PROVINCE = {_norm(nome): (nome, regione) for nome, regione in NUTS3_PROVINCIA.values()}
_PROVINCE.update(  # nomi brevi usati dalla piattaforma
    {
        "reggio emilia": _PROVINCE.get("reggio nell emilia", ("", "")),
        "reggio calabria": _PROVINCE.get("reggio di calabria", ("", "")),
        "monza": _PROVINCE.get("monza e della brianza", ("", "")),
        "pesaro": _PROVINCE.get("pesaro e urbino", ("", "")),
        "forli": _PROVINCE.get("forli", ("", "")),
    }
)


def provincia_e_regione(nome: str | None) -> tuple[str | None, str | None]:
    """«Firenze» → («Firenze», «Toscana»); una regione («Lazio») → (None, «Lazio»)."""
    if not nome:
        return None, None
    trovata = _PROVINCE.get(_norm(nome))
    if trovata and trovata[0]:
        return trovata
    from app.integrazioni.registro_imprese import regione_da_testo

    return None, regione_da_testo(nome)


def _testo(valore: Any) -> str | None:
    if not valore:
        return None
    t = " ".join(html.unescape(str(valore)).split())
    return t or None


def _giorno(valore: str | None) -> date | None:
    """Data in ora italiana da «2026-12-10T12:00:00.000+00:00»."""
    if not valore:
        return None
    try:
        d = datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return d.date()
    return d.astimezone(FUSO).date()


def codice_soa(codice: str | None) -> str | None:
    """«OG 1» → «OG1», «OS 18-B» → «OS18-B»."""
    c = re.sub(r"\s", "", (codice or "").upper())
    m = re.fullmatch(r"(O[GS])(\d{1,2})(?:-?([AB]))?", c)
    if not m:
        return None
    return f"{m.group(1)}{int(m.group(2))}" + (f"-{m.group(3)}" if m.group(3) else "")


MINUSCOLE = {"a", "da", "di", "del", "della", "dei", "delle", "e", "in", "sul", "sulla", "al",
             "d", "l", "nell", "dell", "sull", "all", "nel", "sui", "sotto", "sopra"}  # fmt: skip


def nome_comune(nome: str) -> str:
    """«FORNOVO DI TARO» → «Fornovo di Taro»; «SANT'ANGELO» → «Sant'Angelo»."""
    parole = re.split(r"(\W+)", nome.lower())
    return "".join(
        p if (i and p in MINUSCOLE) or not p.isalpha() else p.capitalize()
        for i, p in enumerate(parole)
    )


def numero_ted(link: str | None) -> str | None:
    """«…TED:NOTICE:810170-2026:TEXT…» → «810170-2026» (come l'id_esterno delle gare TED)."""
    m = re.search(r"NOTICE:0*(\d+-\d{4})", link or "")
    return m.group(1) if m else None


def _sezioni(avviso: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict]]:
    """(metadati, campi delle sezioni A e B, lotti) del primo modello in italiano."""
    modelli = avviso.get("templates") or []
    modello = next((m for m in modelli if m.get("lingua") == "it"), modelli[0] if modelli else {})
    t = modello.get("template") or {}
    campi: dict[str, Any] = {}
    lotti: list[dict[str, Any]] = []
    for s in t.get("sections") or []:
        campi.update(s.get("fields") or {})
        lotti += [i for i in s.get("items") or [] if isinstance(i, dict)]
    return t.get("metadata") or {}, campi, lotti


def interpreta(avviso: dict[str, Any], oggi: date, territorio: Any = None) -> dict[str, Any] | None:
    """I campi della gara, o None se l'avviso non serve (oscurato, scaduto, senza lotti)."""
    from app.appalti.scheda import CATEGORIE_SOA

    if avviso.get("oscurato") or avviso.get("attivo") is False:
        return None
    meta, campi, lotti = _sezioni(avviso)
    if not lotti:
        return None
    scadenza = _giorno(avviso.get("dataScadenza")) or min(
        (d for d in (_giorno(lo.get("termine_ricezione")) for lo in lotti) if d), default=None
    )
    if scadenza is None or scadenza < oggi:
        return None
    titolo = (
        _testo(meta.get("titolo")) or _testo(meta.get("descrizione"))
        or _testo(lotti[0].get("descrizione")) or "Gara senza oggetto"
    )  # fmt: skip
    descrizione = _testo(meta.get("descrizione"))
    if len(lotti) > 1:
        righe = [f"Lotto {i}: {_testo(lo.get('descrizione')) or ''}" for i, lo in
                 enumerate(lotti, 1)]  # fmt: skip
        descrizione = "\n".join([descrizione or ""] + righe).strip()
    cpv, divisioni = [], []
    for lo in lotti:
        codice = (str(lo.get("cpv") or "").split("_", 1)[0]).strip()
        if re.fullmatch(r"\d{8}", codice):
            cpv.append(codice)
            if codice[:2] in DIVISIONI_CPV:
                divisioni.append(codice[:2])
    province, regioni = [], []
    for lo in lotti:
        p, r = provincia_e_regione(lo.get("luogo_nuts"))
        if p is None and territorio is not None and lo.get("luogo_istat"):
            # spesso c'è solo il comune («SETTEFRATI»): provincia e regione dalla tabella ISTAT
            voce = territorio.comune(str(lo["luogo_istat"]))
            if voce is not None:
                p, r = provincia_e_regione(voce.provincia)
                r = r or voce.regione
        if p:
            province.append(p)
        if r:
            regioni.append(r)
    importo = Decimal(0)
    for lo in lotti:
        try:
            importo += Decimal(str(lo.get("valore_complessivo_stimato") or 0))
        except InvalidOperation:
            continue
    categorie: dict[str, dict[str, Any]] = {}
    for lo in lotti:
        for c in lo.get("categorie") or []:
            codice = codice_soa(c.get("codice"))
            if codice and codice not in categorie:
                categorie[codice] = {
                    "codice": codice,
                    "descrizione": CATEGORIE_SOA.get(codice) or _testo(c.get("descrizione")),
                    "tipo": None,
                    "importo": None,
                    "provenienza": "anac",
                }
    soggetti = [
        _testo(s.get("denominazione_amministrazione")) for s in campi.get("soggetti_sa") or []
    ]
    documenti = _testo(campi.get("documenti_di_gara_link")) or _testo(
        lotti[0].get("documenti_di_gara_link")
    )
    nature = sorted(
        {NATURA[n] for lo in lotti if (n := str(lo.get("natura_principale") or "").lower()) in
         NATURA}
    )  # fmt: skip
    comuni = [nome_comune(c) for c in dict.fromkeys(_testo(lo.get("luogo_istat")) for lo in lotti)
              if c]  # fmt: skip
    cig = next((str(lo["cig"]).upper() for lo in lotti if lo.get("cig")), None)
    return {
        "titolo": titolo[:2000],
        "stazione_appaltante": " / ".join(dict.fromkeys(s for s in soggetti if s)) or None,
        "citta": comuni[0] if comuni else None,
        "regioni": sorted(set(regioni)) or None,
        "province": sorted(set(province)) or None,
        "cpv": list(dict.fromkeys(cpv)) or None,
        "divisioni_cpv": sorted(set(divisioni)) or None,
        "natura": nature or None,
        "scadenza": scadenza,
        "pubblicazione": _giorno(avviso.get("dataPubblicazione")),
        "importo_stimato": importo if 0 < importo < Decimal("1e14") else None,
        "link": f"{BASE}/bandi/{avviso['idAvviso']}",
        "cig": cig,
        "tipo_procedura": _testo(campi.get("tipo_procedura_aggiudicazione")),
        "criterio_aggiudicazione": _testo(lotti[0].get("criteri_aggiudicazione")),
        "descrizione": descrizione,
        "luogo_esecuzione": ", ".join(comuni[:3]) or None,
        "documenti": [{"titolo": "Documenti di gara", "url": documenti}]
        if documenti and documenti.startswith(("http://", "https://"))
        else None,
        "categorie_soa": list(categorie.values()) or None,
        "codici_soa": sorted(categorie) or None,
        "raw": {
            "idAvviso": avviso.get("idAvviso"),
            "idAppalto": avviso.get("idAppalto"),
            "codiceScheda": avviso.get("codiceScheda"),
            "tipo": avviso.get("tipo"),
            "ted": numero_ted(meta.get("link_eform_ted")),
            "cig": [str(lo.get("cig")) for lo in lotti if lo.get("cig")],
        },
    }


def elenco_del_giorno(client: httpx.Client, giorno: date) -> list[dict[str, Any]]:
    from app.sources import http

    giorno_txt = f"{giorno:%d/%m/%Y}"
    avvisi: list[dict[str, Any]] = []
    for pagina in range(MAX_PAGINE_PER_GIORNO):
        http._attendi_turno(ELENCO)
        r = client.get(
            ELENCO,
            params={
                "dataPubblicazioneStart": giorno_txt,
                "dataPubblicazioneEnd": giorno_txt,
                "codiceScheda": SCHEDE_BANDI,
                "page": str(pagina),
                "size": str(PER_PAGINA),
            },
        )
        if r.status_code >= 400:
            raise ErrorePvl(f"ANAC ha risposto {r.status_code} per il {giorno_txt}")
        dati = r.json()
        avvisi += dati.get("content") or []
        if dati.get("last", True) or not dati.get("content"):
            break
    return avvisi


def dettaglio(client: httpx.Client, id_avviso: str) -> dict[str, Any] | None:
    from app.sources import http

    url = f"{ELENCO}/{id_avviso}"
    http._attendi_turno(url)
    r = client.get(url)
    if r.status_code >= 400:
        return None
    return r.json()


def _id(avviso: dict[str, Any]) -> str:
    return f"{PREFISSO}{avviso.get('idAppalto') or avviso.get('idAvviso')}"[:40]


def raccogli(db: Session, client: httpx.Client | None = None, oggi: date | None = None) -> str:
    """Legge gli avvisi pubblicati negli ultimi giorni e aggiorna la tabella appalto."""
    from app.appalti.scheda import unisci_soa
    from app.sources import http

    oggi = oggi or datetime.now(FUSO).date()
    giorni = GIORNI_ELENCO
    from app.riferimenti import territorio as rif_territorio

    territorio = rif_territorio.in_memoria(db)
    proprio = client is None
    client = client or http.client()
    try:
        avvisi: list[dict[str, Any]] = []
        for n in range(giorni):
            avvisi += elenco_del_giorno(client, oggi - timedelta(days=n))
        # gli avvisi più recenti per primi: una rettifica prevale sull'avviso originale
        avvisi.sort(key=lambda a: a.get("dataPubblicazione") or "", reverse=True)
        esistenti = {a.id_esterno: a for a in db.scalars(select(Appalto))}
        per_cig = {a.cig: a for a in esistenti.values() if a.cig}
        visti: set[str] = set()
        nuovi = aggiornati = arricchiti = dettagli = rimandati = 0
        ora = datetime.now(UTC)
        for avviso in avvisi:
            chiave = _id(avviso)
            if chiave in visti:
                continue
            visti.add(chiave)
            dati = interpreta(avviso, oggi, territorio)
            if dati is None:
                continue
            gia = esistenti.get(chiave)
            ted = esistenti.get(dati["raw"]["ted"] or "") or per_cig.get(dati["cig"] or "")
            if ted is not None and ted.fonte != "anac":
                # sopra soglia, già da TED: si aggiungono CIG e categorie SOA
                ted.cig = ted.cig or dati["cig"]
                categorie = unisci_soa(ted.categorie_soa, dati["categorie_soa"])
                ted.categorie_soa = categorie
                ted.codici_soa = sorted({c["codice"] for c in categorie or []}) or None
                arricchiti += 1
                continue
            if dati["raw"]["ted"] and gia is None:
                continue  # sopra soglia: la porterà TED, senza doppioni
            if gia is not None and (gia.raw or {}).get("idAvviso") == avviso.get("idAvviso"):
                continue  # stesso avviso già salvato: niente di nuovo
            if dettagli >= MAX_DETTAGLI:
                rimandati += 1
                continue
            completo = dettaglio(client, avviso["idAvviso"])
            dettagli += 1
            if completo:
                dati = interpreta(completo, oggi, territorio) or dati
            if gia is None:
                gia = per_cig.get(dati["cig"] or "")  # importata a mano dal file ANAC
            if gia is None:
                a = Appalto(fonte="anac", id_esterno=chiave, visto_il=ora, **dati)
                db.add(a)
                esistenti[chiave] = a
                if a.cig:
                    per_cig[a.cig] = a
                nuovi += 1
            else:
                categorie = unisci_soa(gia.categorie_soa, dati["categorie_soa"])
                dati["categorie_soa"] = categorie
                dati["codici_soa"] = sorted({c["codice"] for c in categorie or []}) or None
                for k, v in dati.items():
                    if v is not None or k not in ("documenti", "categorie_soa"):
                        setattr(gia, k, v)
                aggiornati += 1
        db.commit()
    finally:
        if proprio:
            client.close()
    return (
        f"ANAC (pubblicità legale): {len(avvisi)} avvisi letti in {giorni} giorni, {nuovi} gare "
        f"nuove, {aggiornati} aggiornate, {arricchiti} gare TED completate con CIG e SOA"
        + (f", {rimandati} rimandate alla prossima notte." if rimandati else ".")
    )
