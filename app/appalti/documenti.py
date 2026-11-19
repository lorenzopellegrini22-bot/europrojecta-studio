# ruff: noqa: E501
"""Documentazione di ogni gara: bando, disciplinare, capitolato, domanda di partecipazione,
modulistica, allegati, avviso pubblicato su TED (Bando GUUE).

Da dove arriva (verificato il 18/11/2026 sulle gare aperte):
- TED dà il PDF dell'avviso: https://ted.europa.eu/it/notice/{numero}/pdf (robots.txt lo
  consente) e il link alla pagina della gara sulla piattaforma della stazione appaltante;
- dalla pagina della gara si leggono i documenti elencati, se la piattaforma li pubblica in
  HTML: link diretti ai file (es. Asmecomm, Soresa) oppure link costruiti in JavaScript con
  window.open(… + escape(…) + …) (piattaforme «DettaglioProcedura.asp», es. SardegnaCAT,
  Giada). Le piattaforme fatte solo in JavaScript (es. START Toscana) o i link alla sola home
  (es. Sintel, MEPA) non hanno documenti leggibili: resta il link alla pagina della gara.

Regole: si rispetta il robots.txt di ogni sito, una richiesta alla volta per dominio, si
leggono solo le pagine (i file non si scaricano: li apre chi clicca, dal sito dell'ente).
"""

import html
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Appalto

log = logging.getLogger(__name__)

MAX_DOCUMENTI = 40
GIORNI_TRA_LETTURE = 7
ESTENSIONI = r"pdf|p7m|zip|rar|7z|docx?|xlsx?|odt|ods|rtf|dwg"
_FILE = re.compile(rf"\.({ESTENSIONI})(?:\b|$)", re.IGNORECASE)
_LINK_FILE = re.compile(
    r"displayattach|/allegati/open\.php|getfile|download(?:file|allegato)?[/?.=]|"
    r"scaricadocumento|/documento/\d|attachment",
    re.IGNORECASE,
)
_WINDOW_OPEN = re.compile(
    r"window\.open\('([^']+)'\s*\+\s*escape\('([^']+)'\)\s*\+\s*'([^']*)'", re.IGNORECASE
)

# Tipi di documento, nell'ordine in cui si mostrano (parole cercate nel titolo)
TIPI: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bando GUUE", ("guue", "ojs", "ted.europa", "avviso pubblicato su ted")),
    ("Bando di gara", ("bando", "avviso di gara", "lettera di invito")),
    ("Disciplinare", ("disciplinare",)),
    ("Capitolato", ("capitolato",)),
    ("Domanda di partecipazione", ("domanda di partecipazione", "istanza di partecipazione", "domanda")),
    ("DGUE", ("dgue", "documento di gara unico")),
    ("Offerta economica e tecnica", ("offerta", "parametri", "elenco prodotti", "lista lavorazioni")),
    ("Modulistica e dichiarazioni", ("modello", "modulo", "dichiarazion", "avvaliment", "garanzia", "patto di integrit", "scheda cig", "allegato a", "fac simile", "facsimile")),
    ("Schema di contratto", ("schema di contratto", "contratto")),
    ("Progetto ed elaborati tecnici", ("progetto", "elaborat", "relazione", "computo", "tavol", "quadro tecnico", "piano")),
    ("Atti dell'ente", ("determin", "delibera", "decreto")),
    ("Chiarimenti", ("chiariment", "faq", "rettifica", "proroga")),
)  # fmt: skip


def tipo_documento(titolo: str) -> str:
    t = titolo.lower().replace("_", " ").replace("-", " ")
    for tipo, parole in TIPI:
        if any(p in t for p in parole):
            return tipo
    return "Altri allegati"


def _titolo(testo: str, url: str) -> str:
    testo = " ".join((testo or "").split())
    if (
        testo
        and len(testo) > 2
        and testo.lower() not in ("scarica", "download", "apri", "visualizza")
    ):
        return testo[:200]
    nome = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return nome[:200] or "Documento"


def estrai(pagina: str, base: str) -> list[dict[str, str]]:
    """Documenti elencati in una pagina di gara: [{"titolo", "url", "tipo"}]."""
    trovati: dict[str, dict[str, str]] = {}
    zuppa = BeautifulSoup(pagina, "html.parser")
    for a in zuppa.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        testo = a.get_text(" ")
        if not (_FILE.search(href) or _LINK_FILE.search(href) or _FILE.search(testo)):
            continue
        try:
            url = urljoin(base, href)
        except ValueError:
            continue
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        titolo = _titolo(testo, url)
        trovati.setdefault(url, {"titolo": titolo, "url": url, "tipo": tipo_documento(titolo)})
    # Link costruiti in JavaScript (piattaforme «DettaglioProcedura.asp»)
    for prima, valore, dopo in _WINDOW_OPEN.findall(html.unescape(pagina)):
        if "displayattach" not in prima.lower():
            continue
        try:
            url = urljoin(base, prima + quote(valore, safe="@*_+-./") + dopo)
        except ValueError:
            continue
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        titolo = valore.split("*", 1)[0] or "Allegato"
        trovati.setdefault(url, {"titolo": titolo, "url": url, "tipo": tipo_documento(titolo)})
    return list(trovati.values())[:MAX_DOCUMENTI]


def pagina_specifica(url: str | None) -> bool:
    """Il link porta alla pagina di una gara (non alla sola home della piattaforma)?"""
    if not url:
        return False
    parti = urlsplit(url)
    percorso = parti.path.strip("/")
    if parti.fragment and "/" in parti.fragment:  # app JavaScript (#/procedura?...)
        return False
    return (
        bool(parti.query)
        or percorso.count("/") >= 1
        and not percorso.lower().endswith(("home.aspx", "index.html", "index.php"))
    )


def pdf_ted(a: Appalto) -> str | None:
    """PDF dell'avviso su TED (Bando GUUE), dal numero di pubblicazione (es. 400184-2024)."""
    if a.fonte == "ted" and re.fullmatch(r"\d+-\d{4}", a.id_esterno or ""):
        return f"https://ted.europa.eu/it/notice/{a.id_esterno}/pdf"
    return None


def documentazione(a: Appalto) -> list[dict[str, str]]:
    """Elenco da mostrare: avviso TED, documenti letti dalla piattaforma, pagina della gara,
    piattaforma per l'offerta. Ordinati per tipo come nelle schede gara."""
    elenco: list[dict[str, str]] = []
    if (ted := pdf_ted(a)) is not None:
        elenco.append(
            {"titolo": "Bando GUUE (avviso pubblicato su TED)", "url": ted, "tipo": "Bando GUUE"}
        )
    for d in a.documenti or []:
        if d.get("url") and d["url"] not in {x["url"] for x in elenco}:
            elenco.append({**d, "tipo": d.get("tipo") or tipo_documento(d.get("titolo") or "")})
    ordine = {t: i for i, (t, _p) in enumerate(TIPI)}
    elenco.sort(key=lambda d: ordine.get(d["tipo"], len(ordine)))
    return elenco


def leggi_gara(a: Appalto, client: httpx.Client, robots) -> str:  # noqa: ANN001
    """Legge la pagina della gara e aggiorna a.documenti. Restituisce l'esito in breve."""
    from app.sources import http

    # Si rileggono solo le pagine della gara (link di TED e pagina già trovata), mai i file
    # elencati: quelli li apre chi clicca. Ordine stabile: prima il link di partecipazione.
    candidati = [a.link_partecipazione] + [
        d.get("url")
        for d in a.documenti or []
        if d.get("tipo") == "Pagina della gara" or d.get("titolo") == "Documenti di gara"
    ]
    pagine = [u for u in dict.fromkeys(candidati) if pagina_specifica(u) and not _FILE.search(u)]
    if not pagine:
        a.documenti_letti_il = datetime.now(UTC)
        return "nessuna pagina della gara (link alla sola piattaforma)"
    nuovi: list[dict[str, str]] = []
    esito = "nessun documento leggibile nella pagina"
    for url in pagine[:2]:
        if not robots.permesso(url):
            esito = "il sito vieta la lettura automatica (robots.txt)"
            continue
        try:
            letta = http.pagina_html(client, url)
        except (http.ErroreDownload, httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
            esito = f"pagina non raggiungibile ({type(e).__name__})"
            continue
        if letta is None:  # non è una pagina ma un file: non si scarica
            continue
        trovati = estrai(*letta)
        nuovi += [d for d in trovati if d["url"] not in {x["url"] for x in nuovi}]
    pagina_gara = [
        {"titolo": "Pagina della gara sulla piattaforma", "url": u, "tipo": "Pagina della gara"}
        for u in pagine[:1]
    ]
    if nuovi:
        a.documenti = (nuovi + pagina_gara)[:MAX_DOCUMENTI]
        esito = f"{len(nuovi)} documenti"
    a.documenti_letti_il = datetime.now(UTC)
    return esito


def aggiorna(db: Session, limite: int = 150, client: httpx.Client | None = None) -> str:
    """Ogni notte: documentazione delle gare aperte non lette negli ultimi 7 giorni."""
    from app.documenti.servizio import Robots
    from app.sources import http

    soglia = datetime.now(UTC) - timedelta(days=GIORNI_TRA_LETTURE)
    gare = list(
        db.scalars(
            select(Appalto)
            .where(
                Appalto.scadenza >= date.today(),
                or_(Appalto.documenti_letti_il.is_(None), Appalto.documenti_letti_il < soglia),
            )
            .order_by(Appalto.scadenza)
            .limit(limite)
        )
    )
    proprio = client is None
    client = client or http.client()
    robots = Robots(client)
    con_documenti = 0
    try:
        for a in gare:
            try:
                if leggi_gara(a, client, robots).endswith("documenti"):
                    con_documenti += 1
            except Exception:  # un link strano non deve fermare la notte
                log.exception("Documentazione della gara %s non letta", a.id)
                db.rollback()
                a.documenti_letti_il = datetime.now(UTC)
            db.commit()
    finally:
        if proprio:
            client.close()
    return f"Documentazione delle gare: {len(gare)} lette, {con_documenti} con documenti."
