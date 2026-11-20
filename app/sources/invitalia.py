"""Fonte nazionale: incentivi gestiti da Invitalia (verifica del 20/11/2026).

Perché: il file open data di Incentivi Italia non contiene alcune misure di Invitalia molto
richieste (Resto al Sud 2.0, Autoimpiego Centro-Nord, Brevetti+, Voucher cloud…).

Come si raccoglie (dettagli in docs/fonti/invitalia.md):
- due pagine elenco pubbliche, in HTML (niente JavaScript): /per-le-imprese/incentivi-e-strumenti
  e /per-chi-vuole-fare-impresa/incentivi-e-strumenti, paginate con ?page=0,1,…;
- ogni scheda dell'elenco ha titolo, stato («Attivo», «In apertura», «Chiuso»), una riga di
  descrizione, «Data apertura» e «Data chiusura»: basta l'elenco, una dozzina di richieste.
- robots.txt (20/11/2026): vieta solo aree tecniche (/admin/, /search/, /user/…), non queste
  pagine. Le note legali vietano la riproduzione dei contenuti a uso commerciale: si salvano
  solo i dati essenziali (titolo, stato, date, la riga di presentazione) e il link alla pagina
  ufficiale, citando la fonte; i PDF e i testi si leggono dalla pagina ufficiale come per gli
  altri bandi.
"""

import re
from pathlib import Path
from typing import Any

import httpx

from app.sources import http
from app.sources.base import RecordBando, RequisitiFonte
from app.sources.regioni import comune
from app.sources.regioni.comune import ErroreFormato
from app.utils.territorio import REGIONI

CODICE = "invitalia"
NOME = "Invitalia (incentivi nazionali)"
BASE = "https://www.invitalia.it"
URL = f"{BASE}/per-le-imprese/incentivi-e-strumenti"
ELENCHI = (
    f"{BASE}/per-le-imprese/incentivi-e-strumenti",
    f"{BASE}/per-chi-vuole-fare-impresa/incentivi-e-strumenti",
)
ATTRIBUZIONE = "Dati: Invitalia (www.invitalia.it). Fa fede la pagina ufficiale dell'incentivo."
STATI = {"attivo": "aperto", "in apertura": "in_arrivo", "chiuso": "chiuso"}
# Misure riservate a una parte d'Italia, riconoscibili dal nome
SUD = [
    "Abruzzo", "Basilicata", "Calabria", "Campania", "Molise", "Puglia", "Sardegna", "Sicilia",
]  # fmt: skip
CENTRO_NORD = [r for r in REGIONI if r not in SUD]
_SUD = re.compile(r"\b(al sud|mezzogiorno|sud italia)\b", re.IGNORECASE)
_CENTRO_NORD = re.compile(r"\bcentro[\s-]?nord\b", re.IGNORECASE)
# voci dell'elenco che non sono incentivi per le imprese (servizi di Invitalia stessa)
_NON_INCENTIVI = re.compile(r"\b(albi? fornitori|gare telematiche|ingate)\b", re.IGNORECASE)


def _testo(nodo: Any) -> str:
    return " ".join(nodo.get_text(" ", strip=True).split()) if nodo is not None else ""


def schede(testo_html: str) -> list[dict[str, Any]]:
    """Le schede degli incentivi in una pagina elenco."""
    s = comune.zuppa(testo_html)
    out: list[dict[str, Any]] = []
    for corpo in s.select("div.card-body"):
        titolo = corpo.select_one("h3 a[href^='/incentivi-e-strumenti/']")
        if titolo is None:
            continue
        slug = titolo["href"].rstrip("/").rsplit("/", 1)[-1]
        stato = _testo(corpo.select_one(".category-top .category")) or _testo(
            corpo.select_one(".dateContainer .category")
        )
        presentazione = corpo.select_one("h3 + p")
        out.append(
            {
                "slug": slug,
                "titolo": _testo(titolo),
                "stato": stato.lower() or None,
                "presentazione": _testo(presentazione) or None,
                "apertura": _testo(corpo.select_one(".dateContainer .apertura")) or None,
                "chiusura": _testo(corpo.select_one(".dateContainer .chiusura")) or None,
            }
        )
    return out


def ultima_pagina(testo_html: str) -> int:
    numeri = [int(n) for n in re.findall(r"[?&]page=(\d+)", testo_html)]
    return max(numeri) if numeri else 0


def raccogli(client: httpx.Client) -> dict[str, dict[str, Any]]:
    tutte: dict[str, dict[str, Any]] = {}
    for elenco in ELENCHI:
        primo = http.pagina(client, elenco).text
        trovate = schede(primo)
        if not trovate:
            raise ErroreFormato(f"Invitalia: nessun incentivo riconosciuto in {elenco}")
        for n in range(1, min(ultima_pagina(primo), comune.MAX_PAGINE_ELENCO) + 1):
            trovate += schede(http.pagina(client, elenco, params={"page": str(n)}).text)
        for sc in trovate:
            tutte.setdefault(sc["slug"], sc)
    return tutte


def regioni_dal_nome(titolo: str) -> list[str] | None:
    if _CENTRO_NORD.search(titolo):
        return CENTRO_NORD
    if _SUD.search(titolo):
        return SUD
    return None


def record(sc: dict[str, Any]) -> RecordBando:
    titolo = sc["titolo"]
    regioni = regioni_dal_nome(titolo)
    req = RequisitiFonte(territorio="regionale" if regioni else "nazionale", regioni=regioni)
    # La scheda non dice beneficiari, importi e territorio esatto: li ricava l'AI dai documenti
    req.da_verificare = [
        "territorio", "dimensioni", "ateco", "importi", "forma_agevolazione",
        "regime", "modalita", "categorie_spesa",
    ]  # fmt: skip
    chiusura = comune.data_italiana(sc.get("chiusura"))
    link = f"{BASE}/incentivi-e-strumenti/{sc['slug']}"
    return RecordBando(
        id_esterno=f"invitalia-{sc['slug']}"[:100],
        titolo=titolo,
        raw={**sc, "link": link},
        ente="Invitalia",
        descrizione=sc.get("presentazione"),
        link_ufficiale=link,
        data_apertura=comune.data_italiana(sc.get("apertura")),
        data_chiusura=chiusura,
        note_apertura_chiusura=None if chiusura else "Data di chiusura non indicata da Invitalia.",
        stato_fonte=STATI.get(sc.get("stato") or ""),
        requisiti=req,
    )


class Invitalia:
    codice = CODICE
    nome = NOME
    url = URL
    soglia_sanita = 0.5  # meno della metà degli incentivi della volta prima: pagina cambiata
    assenze_per_scomparsa = 2

    def versione_remota(self, client: httpx.Client) -> str | None:
        return None

    def scarica(self, client: httpx.Client) -> Path:
        return comune.salva_pacchetto({"schede": raccogli(client)})

    def interpreta(self, percorso: Path) -> list[RecordBando]:
        dati = comune.leggi_pacchetto(percorso)
        return [
            record(sc) for sc in dati["schede"].values() if not _NON_INCENTIVI.search(sc["titolo"])
        ]


FONTE = Invitalia()
