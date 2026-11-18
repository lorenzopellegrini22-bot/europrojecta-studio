"""Funzioni comuni alle fonti regionali (portali WordPress delle regioni e delle loro agenzie).

Le fonti regionali raccolgono solo i bandi che il portale dichiara aperti o in arrivo:
- la lista è piccola, quindi la raccolta è leggera (poche decine di richieste, 1 al secondo);
- un bando che esce dalla lista è chiuso: assenze_per_scomparsa = 1;
- la lista può cambiare molto da un giorno all'altro, quindi non si usa il controllo dell'80%:
  il runner si ferma solo se arrivano 0 bandi dove prima ce n'erano. I cambi di struttura
  delle pagine si riconoscono dai controlli espliciti (ErroreFormato).
"""

import contextlib
import html
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.sources import http

MAX_PAGINE_ELENCO = 30
LOTTO_SLUG = 20
MAX_CARATTERI_DESCRIZIONE = 20_000

MESI = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}
_DATA_NUMERICA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATA_ESTESA = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MESI) + r")\s+(\d{4})\b", re.IGNORECASE)


class ErroreFormato(Exception):
    """La pagina non ha più la struttura attesa: meglio fermarsi che raccogliere dati sbagliati."""


def zuppa(testo_html: str) -> BeautifulSoup:
    return BeautifulSoup(testo_html, "html.parser")


def testo_da_html(frammento: str | None) -> str:
    """Testo leggibile da un frammento HTML: tag rimossi, entità decodificate, spazi compattati."""
    if not frammento:
        return ""
    s = zuppa(frammento)
    for el in s(["script", "style"]):
        el.decompose()
    righe = (r.strip() for r in s.get_text("\n").splitlines())
    testo = "\n".join(r for r in righe if r)
    return re.sub(r"[ \t ]+", " ", testo)


def pulisci(testo: str | None) -> str:
    return html.unescape(testo or "").strip()


def data_italiana(testo: str | None) -> date | None:
    """Prima data trovata nel testo: "18/11/2026" oppure "18 Novembre 2026"."""
    if not testo:
        return None
    candidati: list[tuple[int, date]] = []
    for m in _DATA_NUMERICA.finditer(testo):
        g, me, a = (int(x) for x in m.groups())
        with contextlib.suppress(ValueError):
            candidati.append((m.start(), date(a, me, g)))
    for m in _DATA_ESTESA.finditer(testo):
        try:
            d = date(int(m.group(3)), MESI[m.group(2).lower()], int(m.group(1)))
            candidati.append((m.start(), d))
        except ValueError:
            pass
    return min(candidati)[1] if candidati else None


def slug_da_link(link: str, segmento: str) -> str | None:
    """Slug del post da un URL del tipo https://sito/<segmento>/<slug>/."""
    parti = [p for p in urlsplit(link).path.split("/") if p]
    if len(parti) >= 2 and parti[0] == segmento:
        return parti[1]
    return None


def classi(post: dict[str, Any], prefisso: str) -> list[str]:
    """Termini di una tassonomia dal class_list di WordPress (es. "destinatari-pmi" → "pmi")."""
    p = prefisso + "-"
    return [c[len(p) :] for c in post.get("class_list") or [] if c.startswith(p)]


def mappa(termini: list[str], regole: dict[str, list[str]]) -> list[str] | None:
    """Traduce i termini del portale nel vocabolario comune; None se nessuno è riconosciuto."""
    valori: list[str] = []
    for t in termini:
        for v in regole.get(t, []):
            if v not in valori:
                valori.append(v)
    return valori or None


def post_wordpress(
    client: httpx.Client, base: str, tipo: str, slug: list[str]
) -> dict[str, dict[str, Any]]:
    """Post di un custom post type via REST API standard di WordPress, cercati per slug."""
    trovati: dict[str, dict[str, Any]] = {}
    for i in range(0, len(slug), LOTTO_SLUG):
        lotto = slug[i : i + LOTTO_SLUG]
        r = http.pagina(
            client,
            f"{base}/wp-json/wp/v2/{tipo}",
            params={
                "slug": ",".join(lotto),
                "per_page": "100",
                "_fields": "id,slug,link,title,content,modified,class_list",
            },
        )
        dati = r.json()
        if not isinstance(dati, list):
            raise ErroreFormato(f"{base}: risposta REST inattesa per {tipo}")
        for p in dati:
            if isinstance(p, dict) and p.get("slug"):
                trovati[p["slug"]] = p
    return trovati


def termini_wordpress(client: httpx.Client, base: str, tassonomia: str) -> dict[str, str]:
    """slug → nome dei termini di una tassonomia (al massimo 100, bastano per gli enti)."""
    r = http.pagina(
        client,
        f"{base}/wp-json/wp/v2/{tassonomia}",
        params={"per_page": "100", "_fields": "slug,name"},
    )
    return {t["slug"]: pulisci(t["name"]) for t in r.json() if isinstance(t, dict)}


def salva_pacchetto(dati: dict[str, Any]) -> Path:
    """Il "file scaricato" di una fonte regionale: un JSON con tutto il necessario
    all'interpretazione, così interpreta() resta una funzione pura e testabile."""
    fd, nome = tempfile.mkstemp(prefix="fonte-regionale-", suffix=".json")
    os.close(fd)
    p = Path(nome)
    p.write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    return p


def leggi_pacchetto(percorso: Path) -> dict[str, Any]:
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ErroreFormato(f"Pacchetto non valido: {e}") from e
    if not isinstance(dati, dict):
        raise ErroreFormato("Pacchetto non valido")
    return dati
