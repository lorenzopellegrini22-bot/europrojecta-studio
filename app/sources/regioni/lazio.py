"""Fonte regionale: Regione Lazio, dai due portali ufficiali (verifica del 18/11/2026).

- Lazio Innova (www.lazioinnova.it), agenzia regionale per lo sviluppo: i bandi per le imprese;
- Lazio Europa (www.lazioeuropa.it), portale dei fondi europei della Regione: anche bandi
  per enti, scuole, cittadini e agricoltura.
Molti bandi stanno su entrambi con lo stesso slug: si tiene una sola copia, quella di Lazio
Innova.

Come si raccoglie (dettagli in docs/fonti/regione-lazio.md):
1. pagine elenco /bandi/ filtrate per stato (filtro "stati" del portale), con paginazione;
2. contenuti e tassonomie dalla REST API standard di WordPress (/wp-json/wp/v2/bandi?slug=);
3. nomi degli enti dalla tassonomia "enti" di Lazio Europa.
robots.txt di entrambi: nessun divieto. Le date non sono strutturate (stanno nel testo):
fino all'estrazione LLM (fase 3) lo stato è quello dichiarato dal portale.
"""

from pathlib import Path
from typing import Any

import httpx

from app.sources import http
from app.sources.base import RecordBando, RequisitiFonte
from app.sources.regioni import comune
from app.sources.regioni.comune import ErroreFormato

CODICE = "regione_lazio"
NOME = "Regione Lazio (Lazio Innova e Lazio Europa)"
URL = "https://www.lazioinnova.it/bandi/"

LAZIO_INNOVA = "https://www.lazioinnova.it"
LAZIO_EUROPA = "https://www.lazioeuropa.it"

# Valori del filtro "stati" di ciascun portale
FILTRI_STATO = {
    LAZIO_INNOVA: {"aperto": "aperto", "in_arrivo": "prossima apertura"},
    LAZIO_EUROPA: {"aperto": "1", "in_arrivo": "2"},
}

PMI = ["micro", "piccola", "media"]
DIMENSIONI = {
    "piccole-e-medie-imprese": PMI,
    "piccole-e-medie-imprese-editoriali": PMI,
    "pmi-innovative": PMI,
    "microimprese": ["micro"],
    "micro-imprese": ["micro"],
    "piccole-imprese": ["piccola"],
    "medie-imprese": ["media"],
    "grandi-imprese": ["grande"],
    "midcap": ["grande"],
}
IMPRESA = ["Impresa"]
SOGGETTI = {
    **{k: IMPRESA for k in DIMENSIONI},
    "imprese": IMPRESA,
    "altre-imprese": IMPRESA,
    "imprese-agricole": IMPRESA,
    "imprese-turistiche": IMPRESA,
    "imprese-sociali": IMPRESA,
    "imprese-e-liberi-professionisti": ["Impresa", "Professionista"],
    "operatori-economici-imprese-e-liberi-professionisti": ["Impresa", "Professionista"],
    "imprese-femminili": ["Impresa - prevalenza femminile"],
    "startup": ["Impresa - SU/PMI innovativa"],
    "startup-innovative": ["Impresa - SU/PMI innovativa"],
    "startup-e-spinoff": ["Impresa - SU/PMI innovativa"],
    "imprese-costituende": ["Impresa da costituire - Altro"],
    "liberi-professionisti": ["Professionista"],
    "professionisti": ["Professionista"],
    "reti-dimpresa": ["Rete d'impresa"],
    "aggregazioni-dimpresa": ["Rete d'impresa"],
    "consorzi-di-imprese": ["Consorzio"],
    "cooperative": ["Cooperative/Associazioni Non Profit"],
    "associazioni": ["Cooperative/Associazioni Non Profit"],
    "no-profit": ["Cooperative/Associazioni Non Profit"],
    "onlus": ["Cooperative/Associazioni Non Profit"],
    "terzo-settore": ["Cooperative/Associazioni Non Profit"],
    "comuni": ["Ente Pubblico"],
    "enti-locali": ["Ente Pubblico"],
    "enti-pubblici": ["Ente Pubblico"],
    "pubblica-amministrazione": ["Ente Pubblico"],
    "universita": ["Università/Ente di Ricerca"],
    "universita-e-ricerca": ["Università/Ente di Ricerca"],
    "enti-e-organismi-di-ricerca": ["Università/Ente di Ricerca"],
    "cittadini": ["Cittadino"],
    "persone-fisiche": ["Cittadino"],
}
FONDO_PERDUTO = ["Contributo/Fondo perduto"]
FORME = {
    "fondo-perduto": FONDO_PERDUTO,
    "a-fondo-perduto": FONDO_PERDUTO,
    "contributi-una-tantum": FONDO_PERDUTO,
    "sovvenzione-diretta": FONDO_PERDUTO,
    "voucher": FONDO_PERDUTO,
    "premio": FONDO_PERDUTO,
    "premi-in-denaro-e-servizi": FONDO_PERDUTO,
    "finanziamenti-agevolati": ["Prestito/Anticipo rimborsabile"],
    "finanziamenti-tasso-zero": ["Prestito/Anticipo rimborsabile"],
    "microfinanza": ["Prestito/Anticipo rimborsabile"],
    "garanzie": ["Interventi a garanzia"],
    "capitale-di-rischio": ["Capitale di rischio"],
    "quasi-equity": ["Capitale di rischio"],
    "venture-capital": ["Capitale di rischio"],
    "credito-dimposta": ["Agevolazione fiscale"],
}
FINALITA = {
    "internazionalizzazione": ["Internazionalizzazione"],
    "innovazione": ["Innovazione e ricerca"],
    "ricerca-rs": ["Innovazione e ricerca"],
    "innovazione-competitivita-industria-4-0": ["Innovazione e ricerca"],
    "digitalizzazione": ["Digitalizzazione"],
    "transizione-ecologica": ["Transizione ecologica"],
    "green-economy": ["Transizione ecologica"],
    "economia-circolare": ["Transizione ecologica"],
    "creazione-di-impresa": ["Start up/Sviluppo d'impresa"],
    "investimenti": ["Sostegno investimenti"],
    "accesso-al-credito": ["Sostegno liquidità"],
    "accesso-al-credito-e-alle-garanzie-per-le-imprese": ["Sostegno liquidità"],
    "sostegno-a-imprese-in-crisi": ["Crisi d'impresa"],
}


def elenco_lazio_innova(testo_html: str) -> tuple[list[str], list[int]]:
    """(slug dei bandi, numeri delle altre pagine) da una pagina elenco di Lazio Innova."""
    s = comune.zuppa(testo_html)
    if s.find("select", attrs={"name": "stati"}) is None:
        raise ErroreFormato("Lazio Innova: filtro per stato non trovato nella pagina elenco")
    slug = []
    for art in s.select("article.bandi"):
        a = art.select_one(".entry-title a[href]")
        sl = comune.slug_da_link(a["href"], "bandi") if a else None
        if sl and sl not in slug:
            slug.append(sl)
    return slug, _pagine(s)


def elenco_lazio_europa(testo_html: str) -> tuple[list[str], list[int]]:
    s = comune.zuppa(testo_html)
    if s.find("select", attrs={"name": "stati"}) is None:
        raise ErroreFormato("Lazio Europa: filtro per stato non trovato nella pagina elenco")
    slug = []
    for art in s.select("article.box-bandi"):
        a = art.select_one(".box-bandi-titolo a[href]")
        sl = comune.slug_da_link(a["href"], "bandi") if a else None
        if sl and sl not in slug:
            slug.append(sl)
    return slug, _pagine(s)


def _pagine(s: Any) -> list[int]:
    numeri = set()
    for a in s.select('a[href*="/bandi/page/"]'):
        parti = a["href"].split("/bandi/page/")[1].split("/")[0]
        if parti.isdigit():
            numeri.add(int(parti))
    return sorted(n for n in numeri if n > 1)


def _raccogli_elenco(client: httpx.Client, base: str, filtro: str, lettore) -> list[str]:
    r = http.pagina(client, f"{base}/bandi/", params={"stati": filtro})
    slug, pagine = lettore(r.text)
    for n in pagine[: comune.MAX_PAGINE_ELENCO]:
        r = http.pagina(client, f"{base}/bandi/page/{n}/", params={"stati": filtro})
        altri, _ = lettore(r.text)
        slug += [x for x in altri if x not in slug]
    return slug


def _requisiti(post: dict[str, Any], sito: str) -> RequisitiFonte:
    destinatari = comune.classi(post, "destinatari")
    forme = comune.classi(post, "tipologie-agevolazioni")
    temi = comune.classi(post, "tematiche" if sito == LAZIO_INNOVA else "aree")
    dimensioni = comune.mappa(destinatari, DIMENSIONI)
    if dimensioni:
        dimensioni = [d for d in ["micro", "piccola", "media", "grande"] if d in dimensioni]
    req = RequisitiFonte(
        territorio="regionale",
        regioni=["Lazio"],
        dimensioni=dimensioni,
        tipologie_soggetto=comune.mappa(destinatari, SOGGETTI),
        forma_agevolazione=comune.mappa(forme, FORME),
        finalita=comune.mappa(temi, FINALITA),
    )
    req.da_verificare = ["date", "importi", "ateco", "regime", "modalita", "categorie_spesa"]
    for campo in ("dimensioni", "tipologie_soggetto", "forma_agevolazione"):
        if getattr(req, campo) is None:
            req.da_verificare.append(campo)
    return req


def record_da_post(
    post: dict[str, Any], sito: str, stato: str, enti: dict[str, str]
) -> RecordBando:
    testo = comune.testo_da_html((post.get("content") or {}).get("rendered"))
    if sito == LAZIO_INNOVA:
        ente = "Regione Lazio - Lazio Innova"
    else:
        nomi = [enti.get(e, e) for e in comune.classi(post, "enti")]
        ente = ", ".join(nomi) if nomi else "Regione Lazio"
    sigla = "lazioinnova" if sito == LAZIO_INNOVA else "lazioeuropa"
    raw = {
        "sito": sito,
        "id": post.get("id"),
        "slug": post.get("slug"),
        "link": post.get("link"),
        "titolo": (post.get("title") or {}).get("rendered"),
        "modificato": post.get("modified"),
        "class_list": post.get("class_list"),
        "stato_dichiarato": stato,
        "testo": testo[: comune.MAX_CARATTERI_DESCRIZIONE],
    }
    return RecordBando(
        id_esterno=f"{sigla}-{post['id']}",
        titolo=comune.pulisci((post.get("title") or {}).get("rendered")) or post["slug"],
        raw=raw,
        ente=ente,
        descrizione=testo[: comune.MAX_CARATTERI_DESCRIZIONE] or None,
        link_ufficiale=post.get("link"),
        stato_fonte=stato,
        note_apertura_chiusura=(
            "Date non strutturate sul portale: stato dichiarato dal portale, "
            "date da verificare nel testo del bando."
        ),
        requisiti=_requisiti(post, sito),
    )


class RegioneLazio:
    codice = CODICE
    nome = NOME
    url = URL
    soglia_sanita = 0.0
    assenze_per_scomparsa = 1

    def versione_remota(self, client: httpx.Client) -> str | None:
        return None  # nessuna versione globale: si raccoglie a ogni esecuzione

    def scarica(self, client: httpx.Client) -> Path:
        siti: dict[str, Any] = {}
        for base, lettore in (
            (LAZIO_INNOVA, elenco_lazio_innova),
            (LAZIO_EUROPA, elenco_lazio_europa),
        ):
            stati: dict[str, str] = {}
            for stato, filtro in FILTRI_STATO[base].items():
                for sl in _raccogli_elenco(client, base, filtro, lettore):
                    stati.setdefault(sl, stato)
            siti[base] = {"stati": stati}
        # Una sola copia per i bandi presenti su entrambi i portali
        for sl in list(siti[LAZIO_EUROPA]["stati"]):
            if sl in siti[LAZIO_INNOVA]["stati"]:
                del siti[LAZIO_EUROPA]["stati"][sl]
        for base, dati in siti.items():
            dati["post"] = comune.post_wordpress(client, base, "bandi", list(dati["stati"]))
        enti = comune.termini_wordpress(client, LAZIO_EUROPA, "enti")
        return comune.salva_pacchetto({"siti": siti, "enti": enti})

    def interpreta(self, percorso: Path) -> list[RecordBando]:
        dati = comune.leggi_pacchetto(percorso)
        enti = dati.get("enti") or {}
        record = []
        for base, sito in (dati.get("siti") or {}).items():
            for sl, stato in sito["stati"].items():
                post = sito["post"].get(sl)
                if post is None:
                    continue  # in elenco ma non (ancora) restituito dalla REST API
                record.append(record_da_post(post, base, stato, enti))
        return record


FONTE = RegioneLazio()
