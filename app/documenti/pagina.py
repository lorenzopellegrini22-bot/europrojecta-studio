"""Pagina ufficiale del bando: il suo testo e i PDF che elenca.

Molte fonti (per esempio Incentivi.gov.it) danno solo il link alla pagina dell'ente, senza i
PDF. Ogni notte si leggono alcune di queste pagine (rispettando robots.txt e le pause tra
richieste allo stesso sito):
- si salva il testo della pagina (senza menu e piè di pagina): l'AI lo legge insieme ai PDF,
  e da solo quando i PDF non si possono scaricare (robots.txt, errori del sito). Spesso la
  pagina dice già beneficiari, spese e importi;
- si registrano i PDF utili (bando o avviso, decreto, allegati, FAQ), che il passo dei PDF
  scarica e legge come gli altri.
Prima le pagine dei bandi che non hanno ancora testo, poi quelle lette da più di 14 giorni.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.models import Bando
from app.models.documenti import DocumentoBando

log = logging.getLogger(__name__)

PAGINE_PER_NOTTE = 120
MAX_TESTO_PAGINA = 40_000
MIN_TESTO_PAGINA = 200
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


def testo_della_pagina(pagina: str) -> str | None:
    """Il testo leggibile della pagina: il contenuto principale (<main>, se ha testo), un
    blocco per riga, senza menu, piè di pagina e righe ripetute (pulsanti, briciole).
    I <form> restano: i siti ASP.NET (es. Regione Valle d'Aosta) ci mettono tutta la pagina;
    gli <article> non si scelgono: spesso sono le notizie a lato (es. CCIAA Catanzaro)."""
    zuppa = BeautifulSoup(_contenuto(pagina), "html.parser")
    for el in zuppa.find_all(["button", "select", "input", "textarea", "iframe", "svg", "aside"]):
        el.decompose()
    for el in zuppa.select('[class*="cookie"], [id*="cookie"], [class*="breadcrumb"]'):
        el.decompose()
    principale = zuppa.find("main")
    if principale is not None and len(principale.get_text(" ", strip=True)) >= MIN_TESTO_PAGINA:
        radice = principale
    else:
        radice = zuppa.body or zuppa
    righe: list[str] = []
    viste: set[str] = set()
    for riga in radice.get_text("\n").splitlines():
        riga = " ".join(riga.split())
        if len(riga) < 3 or riga.lower() in viste:
            continue
        viste.add(riga.lower())
        righe.append(riga)
    testo = "\n".join(righe)[:MAX_TESTO_PAGINA]
    return testo if len(testo) >= MIN_TESTO_PAGINA else None


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


HOME = re.compile(r"^(index|home|default|homepage|it|en)(\.(html?|php|aspx?))?$", re.IGNORECASE)


def pagina_del_bando(url: str) -> bool:
    """Il link porta a una pagina precisa e non alla sola home del sito? Basta un pezzo di
    indirizzo: «…camcom.it/bando-voucher-turismo-anno-2026» è la pagina del bando (il
    controllo delle gare, che ne chiede due, scartava metà delle pagine dei bandi)."""
    from urllib.parse import urlsplit

    parti = urlsplit(url)
    if parti.fragment and "/" in parti.fragment:  # app JavaScript (#/procedura?...)
        return False
    ultimo = parti.path.strip("/").rsplit("/", 1)[-1]
    return bool(parti.query) or (bool(ultimo) and not HOME.match(ultimo))


def leggi_pagina(db: Session, b: Bando, client: httpx.Client, robots) -> int:  # noqa: ANN001
    """Legge la pagina ufficiale: ne salva il testo e registra i PDF nuovi. Restituisce quanti
    PDF ha aggiunto."""
    from app.appalti.documenti import _FILE
    from app.sources import http

    b.pagina_letta_il = datetime.now(UTC)
    url = b.link_ufficiale
    if not url or not pagina_del_bando(url) or _FILE.search(url.split("?", 1)[0]):
        return 0  # la sola home del sito, o già un file (lo prende il passo dei PDF)
    if not robots.permesso(url):
        return 0
    try:
        letta = http.pagina_html(client, url)
    except (http.ErroreDownload, httpx.HTTPError, httpx.InvalidURL, ValueError):
        return 0
    if letta is None:
        return 0
    testo = testo_della_pagina(letta[0])
    if testo or b.testo_pagina is None:  # una lettura vuota non cancella quella buona
        b.testo_pagina = testo
    gia = set(db.scalars(select(DocumentoBando.url).where(DocumentoBando.bando_id == b.id)))
    nuovi = 0
    for d in pdf_della_pagina(*letta):
        if d["url"] not in gia:
            db.add(DocumentoBando(bando_id=b.id, url=d["url"], titolo=d["titolo"], tentativi=0))
            gia.add(d["url"])
            nuovi += 1
    return nuovi


def da_leggere(db: Session, limite: int = PAGINE_PER_NOTTE) -> list[Bando]:
    """Bandi attivi con la pagina mai letta o letta da più di 14 giorni. Prima quelli senza
    testo della pagina né PDF letti (l'AI non ha nulla oltre la scheda della fonte), poi gli
    altri; a parità, quelli che chiudono prima."""
    soglia = datetime.now(UTC) - timedelta(days=GIORNI_TRA_LETTURE)
    con_pdf = select(DocumentoBando.bando_id).where(DocumentoBando.testo_estratto.is_not(None))
    senza_niente = case((and_(Bando.testo_pagina.is_(None), Bando.id.not_in(con_pdf)), 0), else_=1)
    return list(
        db.scalars(
            select(Bando)
            .where(
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
                Bando.link_ufficiale.is_not(None),
                or_(Bando.pagina_letta_il.is_(None), Bando.pagina_letta_il < soglia),
            )
            .order_by(senza_niente, Bando.data_chiusura.asc().nulls_last(), Bando.id)
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
