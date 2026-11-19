"""Scarico e lettura dei PDF dei bandi.

Regole (brief, sezione bandi): si scaricano i PDF solo quando il link punta direttamente a un
PDF; niente navigazione dei siti degli enti. In più:
- solo bandi aperti o in arrivo, al massimo MAX_PER_BANDO PDF per bando, scelti per utilità
  (bando, avviso, disciplinare, decreti prima di contatti, modulistica, rendicontazione);
- robots.txt di ogni dominio rispettato (letto una volta per esecuzione);
- una richiesta al secondo per dominio, user agent identificabile, dimensione massima;
- il file deve essere davvero un PDF (intestazione %PDF);
- salvataggio su disco con nome casuale, sha256 e testo estratto (pypdf). I PDF scansionati
  (senza testo) vengono segnalati: l'OCR non è previsto;
- un PDF non scaricabile si ritenta dopo 7 giorni, al massimo 3 volte.
"""

import hashlib
import io
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Bando
from app.models.documenti import DocumentoBando
from app.sources import http
from app.utils import antivirus

log = logging.getLogger(__name__)
# pypdf segnala nei log ogni font non standard: rumore, non errori
logging.getLogger("pypdf").setLevel(logging.ERROR)

MAX_PER_BANDO = 3
MAX_MB = 25
MIN_TESTO = 200  # sotto questa soglia il PDF è considerato senza testo
MAX_PAGINE = 300
OCR_AI_PER_NOTTE = 3
PER_NOTTE = 60
RIPROVA_DOPO = timedelta(days=7)
MAX_TENTATIVI = 3
_PDF = re.compile(r"https?://[^\s\"'<>]+?\.pdf\b", re.IGNORECASE)

# Parole nel nome o nel titolo del file: più utile → più alto
PREFERITI = (
    ("bando", 5),
    ("avviso", 5),
    ("disciplinare", 5),
    ("regolamento", 3),
    ("decreto", 2),
    ("delibera", 2),
    ("allegato-a", 2),
    ("allegato a", 2),
    ("faq", 1),
)
SCARTATI = (
    "contatti",
    "modulistica",
    "modulo",
    "istruzioni",
    "rendicontazione",
    "-rend",
    "gecoweb",
    "privacy",
    "informativa",
    "manuale",
    "graduatoria",
    "esito",
)


def punteggio_pdf(url: str, titolo: str | None) -> int:
    nome = f"{titolo or ''} {url.rsplit('/', 1)[-1]}".lower().replace("_", "-")
    p = sum(v for k, v in PREFERITI if k in nome)
    p -= sum(4 for k in SCARTATI if k in nome)
    return p


def candidati(b: Bando) -> list[dict[str, str]]:
    """PDF da scaricare per un bando, in ordine di utilità (al massimo MAX_PER_BANDO)."""
    trovati: dict[str, str | None] = {}
    raw = b.raw or {}
    for d in raw.get("documenti_pdf") or []:
        if isinstance(d, dict) and d.get("url"):
            trovati.setdefault(d["url"], d.get("titolo"))
    for testo in (b.link_ufficiale, b.link_domande, *_stringhe(raw)):
        for u in _PDF.findall(testo or ""):
            trovati.setdefault(u, None)
    ordinati = sorted(trovati.items(), key=lambda kv: -punteggio_pdf(kv[0], kv[1]))
    return [
        {"url": u, "titolo": t or u.rsplit("/", 1)[-1]}
        for u, t in ordinati
        if punteggio_pdf(u, t) > -4
    ][:MAX_PER_BANDO]


def _stringhe(raw: dict[str, Any]) -> list[str]:
    out = []
    for k, v in raw.items():
        if k in ("testo", "documenti_pdf"):
            continue
        if isinstance(v, str) and ".pdf" in v.lower():
            out.append(v)
    return out


def cartella() -> Path:
    return Path(get_settings().cartella_allegati).resolve().parent / "documenti_bandi"


# --- robots.txt --------------------------------------------------------------------------


@dataclass
class Robots:
    client: httpx.Client
    cache: dict[str, RobotFileParser | None] = field(default_factory=dict)

    def permesso(self, url: str) -> bool:
        try:
            http.indirizzo_pubblico(url)
            parti = urlsplit(url)
        except (http.ErroreDownload, ValueError):
            return False
        dominio = f"{parti.scheme}://{parti.netloc}"
        if dominio not in self.cache:
            regole: RobotFileParser | None = RobotFileParser()
            try:
                r = http.pagina(self.client, f"{dominio}/robots.txt")
                regole.parse(r.text.splitlines())  # type: ignore[union-attr]
            except (http.ErroreDownload, httpx.HTTPError, httpx.InvalidURL):
                regole = None  # robots.txt assente o non leggibile: nessun divieto dichiarato
            self.cache[dominio] = regole
        regole = self.cache[dominio]
        return True if regole is None else regole.can_fetch(http.USER_AGENT, url)


# --- Scarico e testo ---------------------------------------------------------------------


if TYPE_CHECKING:
    from app.documenti.ocr import QuotaOCR


class ErroreDocumento(Exception):
    pass


def scarica_pdf(client: httpx.Client, url: str) -> bytes:
    limite = MAX_MB * 1024 * 1024
    http._attendi_turno(url)
    with client.stream("GET", url) as r:
        if r.status_code >= 400:
            raise ErroreDocumento(f"Il sito ha risposto {r.status_code}.")
        lunghezza = r.headers.get("content-length")
        if lunghezza and lunghezza.isdigit() and int(lunghezza) > limite:
            raise ErroreDocumento(f"PDF troppo grande (oltre {MAX_MB} MB).")
        dati = bytearray()
        for blocco in r.iter_bytes(256 * 1024):
            dati.extend(blocco)
            if len(dati) > limite:
                raise ErroreDocumento(f"PDF troppo grande (oltre {MAX_MB} MB).")
    if not bytes(dati[:1024]).lstrip().startswith(b"%PDF"):
        raise ErroreDocumento("Il link non porta a un PDF.")
    return bytes(dati)


def testo_pdf(
    dati: bytes, db: Session | None = None, quota: "QuotaOCR | None" = None
) -> tuple[str, int]:
    """Testo del PDF. Se è una scansione: OCR con Tesseract dove c'è, altrimenti (con `db`)
    trascrizione con l'AI dello studio, entro la `quota` della notte."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        lettore = PdfReader(io.BytesIO(dati))
        pagine = lettore.pages[:MAX_PAGINE]
        testi = []
        for p in pagine:
            try:
                testi.append(p.extract_text() or "")
            except Exception:  # noqa: BLE001 — una pagina illeggibile non blocca le altre
                testi.append("")
    except (PdfReadError, ValueError, KeyError) as e:
        raise ErroreDocumento(f"PDF non leggibile: {e}") from e
    testo = "\n".join(testi)
    testo = re.sub(r"[ \t ]+", " ", testo)
    testo = re.sub(r"\n{3,}", "\n\n", testo).strip()
    if len(testo) < MIN_TESTO:
        # Probabilmente una scansione: si prova con l'OCR (se Tesseract è installato)
        from app.documenti.ocr import ocr_pdf

        letto = ocr_pdf(dati)
        if len(letto) < MIN_TESTO and db is not None:
            from app.documenti.ocr import ocr_ai

            letto = ocr_ai(dati, db, quota) or letto
        if len(letto) > len(testo):
            testo = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", letto)).strip()
    return testo, len(lettore.pages)


def _salva(dati: bytes) -> str:
    oggi = date.today()
    relativo = Path(f"{oggi:%Y}") / f"{oggi:%m}" / f"{secrets.token_hex(16)}.pdf"
    destinazione = cartella() / relativo
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_bytes(dati)
    return relativo.as_posix()


def elabora(
    db: Session,
    client: httpx.Client,
    robots: Robots,
    doc: DocumentoBando,
    gia_letti: dict[str, DocumentoBando | str] | None = None,
    quota: "QuotaOCR | None" = None,
) -> bool:
    """Scarica e legge un PDF. `gia_letti` (url → documento letto o errore) evita di scaricare
    di nuovo, nella stessa esecuzione, un PDF collegato a più bandi."""
    doc.tentativi += 1
    doc.scaricato_il = datetime.now(UTC)
    gia_letti = gia_letti if gia_letti is not None else {}
    precedente = gia_letti.get(doc.url)
    if isinstance(precedente, str):
        doc.errore = precedente
        return False
    if precedente is not None:
        for campo in (
            "sha256",
            "percorso",
            "mime",
            "dimensione",
            "pagine",
            "testo_estratto",
            "errore",
        ):
            setattr(doc, campo, getattr(precedente, campo))
        return bool(doc.testo_estratto)
    ok = _scarica_e_leggi(db, client, robots, doc, quota)
    gia_letti[doc.url] = doc if doc.sha256 else (doc.errore or "errore")
    return ok


def _scarica_e_leggi(
    db: Session,
    client: httpx.Client,
    robots: Robots,
    doc: DocumentoBando,
    quota: "QuotaOCR | None" = None,
) -> bool:
    if not robots.permesso(doc.url):
        doc.errore = VIETATO_ROBOTS
        doc.tentativi = MAX_TENTATIVI  # non si ritenta
        return False
    try:
        dati = scarica_pdf(client, doc.url)
        testo, pagine = testo_pdf(dati, db, quota)
    except ErroreDocumento as e:
        doc.errore = str(e)
        return False
    except http.ErroreDownload as e:  # indirizzo non consentito (anche dopo un redirect)
        doc.errore = str(e)[:300]
        doc.tentativi = MAX_TENTATIVI
        return False
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        doc.errore = f"Download non riuscito: {type(e).__name__}"
        return False
    doc.sha256 = hashlib.sha256(dati).hexdigest()
    gia = db.scalar(
        select(DocumentoBando).where(
            DocumentoBando.bando_id == doc.bando_id,
            DocumentoBando.sha256 == doc.sha256,
            DocumentoBando.id != doc.id,
        )
    )
    doc.percorso = gia.percorso if gia and gia.percorso else _salva(dati)
    doc.mime = "application/pdf"
    doc.dimensione = len(dati)
    doc.pagine = pagine
    doc.testo_estratto = testo or None
    doc.errore = (
        None
        if len(testo) >= MIN_TESTO
        else "PDF senza testo leggibile (scansione non letta dall'OCR)."
    )
    return bool(testo)


def esegui(db: Session, client: httpx.Client | None = None, limite: int = PER_NOTTE) -> str:
    """Il job notturno: registra i PDF dei bandi attivi e ne scarica al massimo `limite`."""
    bandi = list(
        db.scalars(
            select(Bando).where(
                Bando.stato.in_(("aperto", "in_arrivo")), Bando.bando_canonico_id.is_(None)
            )
        )
    )
    esistenti = {(d.bando_id, d.url): d for d in db.scalars(select(DocumentoBando))}
    for b in bandi:
        for c in candidati(b):
            if (b.id, c["url"]) not in esistenti:
                d = DocumentoBando(bando_id=b.id, url=c["url"], titolo=c["titolo"], tentativi=0)
                db.add(d)
                esistenti[(b.id, c["url"])] = d
    db.commit()
    soglia = datetime.now(UTC) - RIPROVA_DOPO
    attivi = {b.id for b in bandi}
    da_fare = [
        d
        for d in esistenti.values()
        if d.bando_id in attivi
        and d.sha256 is None
        and d.tentativi < MAX_TENTATIVI
        and (d.scaricato_il is None or d.scaricato_il < soglia)
    ][:limite]
    proprio = client is None
    client = client or http.client()
    letti = falliti = 0
    try:
        robots = Robots(client)
        gia_letti: dict[str, DocumentoBando | str] = {}
        from app.documenti.ocr import QuotaOCR

        # Trascrizioni con l'AI per notte: ognuna può durare un minuto (limite di Vercel)
        quota = QuotaOCR(OCR_AI_PER_NOTTE)
        for d in da_fare:
            if elabora(db, client, robots, d, gia_letti, quota):
                letti += 1
            else:
                falliti += 1
            db.commit()
    finally:
        if proprio:
            client.close()
    return f"PDF dei bandi: {letti} letti, {falliti} non riusciti, {len(da_fare)} in coda stanotte."


# --- Testo per l'AI ----------------------------------------------------------------------

PAROLE_CHIAVE = (
    "beneficiari",
    "soggetti ammissibili",
    "requisiti",
    "spese ammissibili",
    "spese ammesse",
    "agevolazion",
    "contributo",
    "fondo perduto",
    "intensità",
    "massimale",
    "de minimis",
    "regolamento (ue)",
    "esenzione",
    "dotazione",
    "ateco",
    "codice ateco",
    "pmi",
    "micro",
    "piccol",
    "medi",
    "scadenza",
    "termine",
    "presentazione delle domande",
    "sportello",
    "graduatoria",
    "click day",
    "esclus",
    "anzianità",
    "costituit",
)


def brani_utili(testo: str, budget: int) -> str:
    """Se il PDF è più lungo del budget, i paragrafi con più parole chiave (nell'ordine
    originale), così l'AI legge le parti con requisiti, spese, importi, regime e termini."""
    if len(testo) <= budget:
        return testo
    # Bandi conformi al bando-tipo del Codice degli incentivi: prima le partizioni utili
    from app.extraction.bando_tipo import riduci

    ridotto = riduci(testo, budget)
    if ridotto is not None:
        return ridotto
    paragrafi = [p.strip() for p in re.split(r"\n\s*\n|(?<=\.)\n", testo) if p.strip()]
    punteggi = []
    for i, p in enumerate(paragrafi):
        basso = p.lower()
        punteggi.append((sum(basso.count(k) for k in PAROLE_CHIAVE), i))
    scelti: set[int] = set()
    usati = 0
    for punti, i in sorted(punteggi, key=lambda x: (-x[0], x[1])):
        if punti == 0:
            break
        lunghezza = len(paragrafi[i]) + 2
        if usati + lunghezza > budget:
            continue
        scelti.add(i)
        usati += lunghezza
    return "\n\n".join(paragrafi[i] for i in sorted(scelti))


def testo_per_ai(db: Session, bando_id: int, budget: int) -> str:
    docs = list(
        db.scalars(
            select(DocumentoBando)
            .where(DocumentoBando.bando_id == bando_id, DocumentoBando.testo_estratto.is_not(None))
            .order_by(DocumentoBando.id)
        )
    )
    non_letti = list(
        db.scalars(
            select(DocumentoBando).where(
                DocumentoBando.bando_id == bando_id,
                DocumentoBando.testo_estratto.is_(None),
                DocumentoBando.errore.is_not(None),
            )
        )
    )
    avviso = ""
    if non_letti:
        # Il link resta: l'AI sa che il bando ha documenti non letti e non li dà per assenti
        elenco = "\n".join(f"- {d.titolo or 'PDF'}: {d.url}" for d in non_letti[:5])
        avviso = (
            "\n\nDOCUMENTI DEL BANDO NON LETTI (il sito non ne consente lo scarico "
            f"automatico):\n{elenco}\nI requisiti che potrebbero essere in questi documenti "
            "vanno segnati da_verificare."
        )
    if not docs or budget <= 0:
        return avviso[: max(budget, 0)]
    parte = budget // len(docs)
    pezzi = []
    for d in docs:
        brani = brani_utili(d.testo_estratto or "", parte)
        pezzi.append(f"\n\nDOCUMENTO PDF: {d.titolo or d.url}\n{brani}")
    return ("".join(pezzi) + avviso)[:budget]


# --- Caricamento manuale -----------------------------------------------------------------

VIETATO_ROBOTS = "Vietato dal robots.txt del sito: non scaricato."


def carica_a_mano(
    db: Session,
    bando_id: int,
    dati: bytes,
    nome_file: str,
    documento: DocumentoBando | None = None,
    letto: tuple[str, int] | None = None,
) -> DocumentoBando:
    """PDF scaricato dal consulente con il browser (es. siti che vietano lo scarico
    automatico) e caricato nell'app: si salva e si legge come quelli scaricati di notte."""
    if len(dati) > MAX_MB * 1024 * 1024:
        raise ErroreDocumento(f"PDF troppo grande (oltre {MAX_MB} MB).")
    if not dati[:1024].lstrip().startswith(b"%PDF"):
        raise ErroreDocumento("Il file non è un PDF.")
    try:
        antivirus.controlla_bytes(dati)
    except (antivirus.FileInfetto, antivirus.AntivirusNonDisponibile) as e:
        raise ErroreDocumento(str(e)) from e
    # letto: testo già estratto (caricamento multiplo: lo stesso file per più bandi)
    testo, pagine = letto if letto is not None else testo_pdf(dati, db)
    if documento is None:
        documento = DocumentoBando(
            bando_id=bando_id,
            url=f"caricato-a-mano:{secrets.token_hex(8)}",
            titolo=nome_file[:200],
            tentativi=0,
        )
        db.add(documento)
    documento.sha256 = hashlib.sha256(dati).hexdigest()
    documento.percorso = _salva(dati)
    documento.mime = "application/pdf"
    documento.dimensione = len(dati)
    documento.pagine = pagine
    documento.testo_estratto = testo or None
    documento.scaricato_il = datetime.now(UTC)
    documento.errore = (
        None
        if len(testo) >= MIN_TESTO
        else "PDF senza testo leggibile (scansione non letta dall'OCR)."
    )
    return documento


def da_caricare(db: Session) -> list[tuple[Bando, DocumentoBando]]:
    """PDF dei bandi attivi che la notte non ha potuto scaricare (robots.txt o errori)."""
    return list(
        db.execute(
            select(Bando, DocumentoBando)
            .join(DocumentoBando, DocumentoBando.bando_id == Bando.id)
            .where(
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
                DocumentoBando.sha256.is_(None),
                DocumentoBando.errore.is_not(None),
            )
            .order_by(Bando.data_chiusura.asc().nulls_last(), Bando.id)
        ).all()
    )


def nome_dal_link(url: str) -> str:
    """Nome del file nel link («…/PNGSLdocumento73863.pdf» → «pngsldocumento73863.pdf»)."""
    from urllib.parse import unquote, urlsplit

    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]).strip().lower()


def nome_scaricato(nome: str) -> str:
    """Nome del file come lo salva il browser, ripulito: «documento (1).pdf» → «documento.pdf»."""
    nome = nome.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return re.sub(r"\s*\(\d+\)(?=\.pdf$)", "", nome)


def documenti_per_nome(db: Session) -> dict[str, list[DocumentoBando]]:
    """Documenti da caricare raggruppati per nome del file nel link (lo stesso PDF può
    servire a più bandi: si carica su tutti)."""
    per_nome: dict[str, list[DocumentoBando]] = {}
    for _b, d in da_caricare(db):
        per_nome.setdefault(nome_dal_link(d.url), []).append(d)
    # Nomi generici («bando.pdf», «allegato1.pdf») usati da link diversi: non si sa a quale
    # bando appartenga il file, quindi niente abbinamento automatico (si carica dalla riga)
    return {nome: docs for nome, docs in per_nome.items() if len({d.url for d in docs}) == 1}
