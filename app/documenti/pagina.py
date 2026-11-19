"""PDF dalla pagina ufficiale del bando.

Molte fonti (per esempio Incentivi.gov.it) danno solo il link alla pagina dell'ente, senza i
PDF. Ogni notte si leggono alcune di queste pagine (rispettando robots.txt e le pause tra
richieste allo stesso sito) e si registrano i PDF utili: bando o avviso, decreto, allegati, FAQ.
Poi il passo dei PDF li scarica e li legge come gli altri. Si legge solo la pagina, mai i file.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Bando
from app.models.documenti import DocumentoBando

log = logging.getLogger(__name__)

PAGINE_PER_NOTTE = 30
GIORNI_TRA_LETTURE = 14
MAX_DALLA_PAGINA = 6

# Documenti del sito, non del bando: di solito nel piè di pagina
ESCLUSI = (
    "note legali",
    "note-legali",
    "note_legali",
    "cookie",
    "accessibil",
    "termini e condizioni",
    "termini_condizioni",
    "privacy",
    "informativa",
    "costituzione",
    "drafting",
    "carta dei servizi",
    "organigramma",
)
_TITOLI_INUTILI = re.compile(r"^\s*(ascolta|scarica|download|pdf|apri|leggi)\s*$", re.IGNORECASE)


def _contenuto(pagina: str) -> str:
    """La pagina senza intestazione, menu e piè di pagina (dove stanno i PDF del sito)."""
    zuppa = BeautifulSoup(pagina, "html.parser")
    for el in zuppa.find_all(["header", "footer", "nav", "script", "style", "noscript"]):
        el.decompose()
    for el in zuppa.select('[role="navigation"], [role="contentinfo"], [class*="readspeaker"]'):
        el.decompose()
    # «Ascolta», «Scarica»...: se lo stesso file ha anche un link con un titolo vero, vale quello
    con_titolo = {
        a["href"]
        for a in zuppa.find_all("a", href=True)
        if not _TITOLI_INUTILI.match(a.get_text(" "))
    }
    for a in zuppa.find_all("a", href=True):
        if _TITOLI_INUTILI.match(a.get_text(" ")) and a["href"] in con_titolo:
            a.decompose()
    return str(zuppa)


def pdf_della_pagina(pagina: str, base: str) -> list[dict[str, str]]:
    """PDF utili elencati nella pagina, dal più utile: [{"titolo", "url"}]."""
    from app.appalti.documenti import estrai
    from app.documenti.servizio import punteggio_pdf

    scelti: dict[str, dict[str, str]] = {}
    for d in estrai(_contenuto(pagina), base):
        url, titolo = d["url"], " ".join(d["titolo"].split())
        if ".pdf" not in url.lower() and "pdf" not in titolo.lower():
            continue
        if _TITOLI_INUTILI.match(titolo):  # solo «Ascolta»: titolo dal nome del file
            titolo = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if any(x in f"{titolo} {url}".lower() for x in ESCLUSI):
            continue
        if punteggio_pdf(url, titolo) <= -4:
            continue
        scelti.setdefault(url, {"titolo": titolo[:300], "url": url})
    ordinati = sorted(scelti.values(), key=lambda d: -punteggio_pdf(d["url"], d["titolo"]))
    return ordinati[:MAX_DALLA_PAGINA]


def leggi_pagina(db: Session, b: Bando, client: httpx.Client, robots) -> int:  # noqa: ANN001
    """Legge la pagina ufficiale e registra i PDF nuovi. Restituisce quanti ne ha aggiunti."""
    from app.appalti.documenti import _FILE, pagina_specifica
    from app.sources import http

    b.pagina_letta_il = datetime.now(UTC)
    url = b.link_ufficiale
    if not url or not pagina_specifica(url) or _FILE.search(url.split("?", 1)[0]):
        return 0  # la sola home del sito, o già un file (lo prende il passo dei PDF)
    if not robots.permesso(url):
        return 0
    try:
        letta = http.pagina_html(client, url)
    except (http.ErroreDownload, httpx.HTTPError, httpx.InvalidURL, ValueError):
        return 0
    if letta is None:
        return 0
    gia = set(db.scalars(select(DocumentoBando.url).where(DocumentoBando.bando_id == b.id)))
    nuovi = 0
    for d in pdf_della_pagina(*letta):
        if d["url"] not in gia:
            db.add(DocumentoBando(bando_id=b.id, url=d["url"], titolo=d["titolo"], tentativi=0))
            gia.add(d["url"])
            nuovi += 1
    return nuovi


def da_leggere(db: Session, limite: int = PAGINE_PER_NOTTE) -> list[Bando]:
    """Bandi attivi senza PDF trovati, con la pagina mai letta o letta da più di 14 giorni.
    Prima quelli che chiudono prima."""
    soglia = datetime.now(UTC) - timedelta(days=GIORNI_TRA_LETTURE)
    con_pdf = select(DocumentoBando.bando_id).where(
        ~DocumentoBando.url.startswith("caricato-a-mano:")
    )
    return list(
        db.scalars(
            select(Bando)
            .where(
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
                Bando.link_ufficiale.is_not(None),
                Bando.id.not_in(con_pdf),
                or_(Bando.pagina_letta_il.is_(None), Bando.pagina_letta_il < soglia),
            )
            .order_by(Bando.data_chiusura.asc().nulls_last(), Bando.id)
            .limit(limite)
        )
    )


def esegui(db: Session, client: httpx.Client, robots, limite: int = PAGINE_PER_NOTTE) -> int:  # noqa: ANN001
    """Passo notturno, prima dei download dei PDF. Restituisce quanti PDF ha trovato."""
    trovati = 0
    for b in da_leggere(db, limite):
        try:
            trovati += leggi_pagina(db, b, client, robots)
        except Exception:  # una pagina strana non deve fermare la notte
            log.exception("Pagina ufficiale del bando %s non letta", b.id)
            db.rollback()
            b.pagina_letta_il = datetime.now(UTC)
        db.commit()
    return trovati
