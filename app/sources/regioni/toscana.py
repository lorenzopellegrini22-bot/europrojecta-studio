"""Fonte regionale: Regione Toscana, dal portale di Sviluppo Toscana (verifica del 18/11/2026).

Sviluppo Toscana S.p.A. (www.sviluppo.toscana.it) è la società della Regione che gestisce
la maggior parte dei bandi per le imprese.

Come si raccoglie (dettagli in docs/fonti/regione-toscana.md):
1. pagina elenco /bandi/ filtrata per stato con i parametri del portale (_stato=aperto,
   _stato=in-arrivo; paginazione _paged=N);
2. si escludono i bandi "aperti" solo per rendicontazione o varianti (_fase=...): sono aperti
   per chi ha già ottenuto il contributo, non sono nuove opportunità;
3. dalla pagina di ogni bando: "Domande dal", "Apertura", "Chiusura" e lo stato;
4. tassonomie (destinatario, tema) dalla REST API standard di WordPress.
robots.txt: assente (404), quindi nessun divieto.
"""

import re
from pathlib import Path
from typing import Any

import httpx

from app.sources import http
from app.sources.base import RecordBando, RequisitiFonte
from app.sources.regioni import comune
from app.sources.regioni.comune import ErroreFormato

CODICE = "regione_toscana"
NOME = "Regione Toscana (Sviluppo Toscana)"
BASE = "https://www.sviluppo.toscana.it"
URL = f"{BASE}/bandi/"

FILTRI_STATO = {"aperto": "aperto", "in_arrivo": "in-arrivo"}
FASI_ESCLUSE = ("rendicontazione", "varianti")

PMI = ["micro", "piccola", "media"]
DIMENSIONI = {"pmi": PMI, "startup": PMI, "grande-impresa": ["grande"]}
SOGGETTI = {
    "pmi": ["Impresa"],
    "grande-impresa": ["Impresa"],
    "startup": ["Impresa - SU/PMI innovativa"],
    "liberi-professionisti": ["Professionista"],
    "pa": ["Ente Pubblico"],
    "ets": ["Cooperative/Associazioni Non Profit"],
    "privati": ["Cittadino"],
}
FINALITA = {
    "internazionalizzazione": ["Internazionalizzazione"],
    "ricerca": ["Innovazione e ricerca"],
    "innovazione-digitale": ["Digitalizzazione"],
    "transizione-ecologica": ["Transizione ecologica"],
    "energia": ["Transizione ecologica"],
    "economia-circolare": ["Transizione ecologica"],
    "giovani": ["Imprenditoria giovanile"],
    "ristori": ["Sostegno liquidità"],
}
_SPORTELLO = re.compile(r"esaurimento (delle )?risorse", re.IGNORECASE)


def elenco(testo_html: str) -> tuple[list[str], int]:
    """(slug dei bandi, numero totale di pagine) da una pagina elenco filtrata."""
    s = comune.zuppa(testo_html)
    if not s.select('[data-name="stato"]'):
        raise ErroreFormato("Sviluppo Toscana: filtro per stato non trovato nella pagina elenco")
    slug = []
    for a in s.select(f'a[href^="{BASE}/bando/"]'):
        sl = comune.slug_da_link(a["href"], "bando")
        if sl and sl not in slug:
            slug.append(sl)
    m = re.search(r'"total_pages":(\d+)', testo_html)
    return slug, int(m.group(1)) if m else 1


def _raccogli_elenco(client: httpx.Client, params: dict[str, str]) -> list[str]:
    slug, pagine = elenco(http.pagina(client, URL, params=params).text)
    for n in range(2, min(pagine, comune.MAX_PAGINE_ELENCO) + 1):
        altri, _ = elenco(http.pagina(client, URL, params={**params, "_paged": str(n)}).text)
        slug += [x for x in altri if x not in slug]
    return slug


def dettagli_pagina(testo_html: str) -> dict[str, Any]:
    """Titolo, stato, date e testo dalla pagina di un bando."""
    s = comune.zuppa(testo_html)
    h1 = s.find("h1")
    righe = comune.testo_da_html(testo_html).splitlines()
    out: dict[str, Any] = {
        "titolo": h1.get_text(" ", strip=True) if h1 else None,
        "stato": None,
        "domande_dal": None,
        "apertura": None,
        "chiusura": None,
    }
    for i, riga in enumerate(righe):
        seguito = " ".join(righe[i : i + 3])
        if riga.startswith("Stato:") and out["stato"] is None:
            out["stato"] = " ".join(seguito.removeprefix("Stato:").split()[:2]).lower()
        elif riga.startswith("Domande dal") and out["domande_dal"] is None:
            out["domande_dal"] = seguito
        elif riga.startswith("Apertura") and out["apertura"] is None:
            out["apertura"] = seguito
        elif riga.startswith("Chiusura") and out["chiusura"] is None:
            out["chiusura"] = seguito
    main = s.find("main") or s
    out["testo"] = comune.testo_da_html(str(main))[: comune.MAX_CARATTERI_DESCRIZIONE]
    return out


def _stato(dichiarato: str | None, filtro: str) -> str:
    if dichiarato:
        if dichiarato.startswith("aperto"):
            return "aperto"
        if dichiarato.startswith("in arrivo"):
            return "in_arrivo"
        if dichiarato.startswith("chiuso"):
            return "chiuso"
    return filtro


def record(
    slug: str, filtro: str, pagina: dict[str, Any], post: dict[str, Any] | None
) -> RecordBando:
    post = post or {}
    apertura = comune.data_italiana(pagina.get("apertura")) or comune.data_italiana(
        pagina.get("domande_dal")
    )
    chiusura = comune.data_italiana(pagina.get("chiusura"))
    note = None
    if chiusura is None and _SPORTELLO.search(pagina.get("testo") or ""):
        note = "Sportello fino a esaurimento risorse."
    destinatari = comune.classi(post, "destinatario")
    dimensioni = comune.mappa(destinatari, DIMENSIONI)
    if dimensioni:
        dimensioni = [d for d in ["micro", "piccola", "media", "grande"] if d in dimensioni]
    req = RequisitiFonte(
        territorio="regionale",
        regioni=["Toscana"],
        dimensioni=dimensioni,
        tipologie_soggetto=comune.mappa(destinatari, SOGGETTI),
        finalita=comune.mappa(comune.classi(post, "tema"), FINALITA),
    )
    req.da_verificare = [
        "importi",
        "ateco",
        "forma_agevolazione",
        "regime",
        "modalita",
        "categorie_spesa",
    ]
    if chiusura is None:
        req.da_verificare.append("date")
    for campo in ("dimensioni", "tipologie_soggetto"):
        if getattr(req, campo) is None:
            req.da_verificare.append(campo)
    titolo = comune.pulisci((post.get("title") or {}).get("rendered")) or pagina.get("titolo")
    link = post.get("link") or f"{BASE}/bando/{slug}/"
    raw = {
        "slug": slug,
        "id": post.get("id"),
        "link": link,
        "class_list": post.get("class_list"),
        "modificato": post.get("modified"),
        "stato_dichiarato": pagina.get("stato"),
        "domande_dal": pagina.get("domande_dal"),
        "apertura": pagina.get("apertura"),
        "chiusura": pagina.get("chiusura"),
        "testo": pagina.get("testo"),
    }
    return RecordBando(
        id_esterno=f"sviluppotoscana-{post['id']}" if post.get("id") else slug[:100],
        titolo=titolo or slug,
        raw=raw,
        ente="Regione Toscana - Sviluppo Toscana",
        descrizione=pagina.get("testo") or None,
        link_ufficiale=link,
        data_apertura=apertura,
        data_chiusura=chiusura,
        note_apertura_chiusura=note,
        stato_fonte=_stato(pagina.get("stato"), filtro),
        requisiti=req,
    )


class RegioneToscana:
    codice = CODICE
    nome = NOME
    url = URL
    soglia_sanita = 0.0
    assenze_per_scomparsa = 1

    def versione_remota(self, client: httpx.Client) -> str | None:
        return None

    def scarica(self, client: httpx.Client) -> Path:
        stati: dict[str, str] = {}
        for stato, filtro in FILTRI_STATO.items():
            for sl in _raccogli_elenco(client, {"_stato": filtro}):
                stati.setdefault(sl, stato)
        esclusi: set[str] = set()
        for fase in FASI_ESCLUSE:
            esclusi |= set(_raccogli_elenco(client, {"_stato": "aperto", "_fase": fase}))
        stati = {sl: st for sl, st in stati.items() if sl not in esclusi}
        pagine = {
            sl: dettagli_pagina(http.pagina(client, f"{BASE}/bando/{sl}/").text) for sl in stati
        }
        post = comune.post_wordpress(client, BASE, "bando", list(stati))
        return comune.salva_pacchetto(
            {"stati": stati, "esclusi": sorted(esclusi), "pagine": pagine, "post": post}
        )

    def interpreta(self, percorso: Path) -> list[RecordBando]:
        dati = comune.leggi_pacchetto(percorso)
        return [
            record(sl, st, dati["pagine"][sl], dati["post"].get(sl))
            for sl, st in dati["stati"].items()
            if sl in dati["pagine"]
        ]


FONTE = RegioneToscana()
