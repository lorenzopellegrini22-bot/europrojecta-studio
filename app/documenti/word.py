"""PDF dei bandi in Word (.docx), per modificarli con Word, LibreOffice o Google Documenti.

Il testo si ricostruisce dal PDF salvato: paragrafi (righe spezzate riunite), titoli (dal corpo
del carattere e dal grassetto), grassetto e corsivo, elenchi e tabelle. Intestazioni e piè di
pagina ripetuti su ogni pagina e i numeri di pagina si tolgono. Se il PDF è una scansione (o il
file non c'è più) si usa il testo letto dall'app (OCR compreso).
"""

import io
import re
import time
from collections import Counter
from typing import Any

MAX_PAGINE = 300
MIN_LINEE_TABELLA = 6
SECONDI_TABELLE = 8
VERSIONE = "word1"  # cambia quando cambia la conversione: le copie salvate si rifanno
_CONTROLLO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd\ufffe\uffff]")
_ELENCO = re.compile(r"^\s*(?:[•●▪◦■□➢►\-–]\s|\(?[a-z]\)\s|\(?\d{1,2}[.)]\s|[ivx]{1,4}\)\s)")
_NUMERO_PAGINA = re.compile(
    r"^\s*(?:pag(?:ina|\.)?\s*)?\d{1,4}(?:\s*(?:di|/)\s*\d{1,4})?\s*$", re.I
)
_DIMENSIONE = re.compile(r"\s*\(\s*pdf\s*[\d.,]+\s*[kmg]b\s*\)\s*$", re.I)


def pulito(testo: str) -> str:
    """Senza i caratteri che un file Word non può contenere."""
    return _CONTROLLO.sub("", testo)


def nome_file(titolo: str | None) -> str:
    """«Allegato 4 - Domanda ( pdf 555.84 KB)» → «Allegato 4 - Domanda.docx»."""
    base = _DIMENSIONE.sub("", titolo or "documento")
    base = re.sub(r"\.pdf$", "", base.strip(), flags=re.I)
    base = "".join(c for c in base if c.isalnum() or c in " ._-()").strip(" .")[:80]
    return f"{base or 'documento'}.docx"


# --- lettura del PDF -------------------------------------------------------------------------


def _righe_blocco(blocco: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Le righe del blocco come liste di pezzi {testo, grassetto, corsivo, corpo}."""
    righe = []
    for riga in blocco.get("lines", []):
        pezzi = []
        for s in riga.get("spans", []):
            testo = pulito(s.get("text", ""))
            if not testo:
                continue
            font = (s.get("font") or "").lower()
            pezzi.append(
                {
                    "testo": testo,
                    "grassetto": bool(s.get("flags", 0) & 16) or "bold" in font,
                    "corsivo": bool(s.get("flags", 0) & 2) or "italic" in font,
                    "corpo": float(s.get("size", 0)),
                }
            )
        if "".join(p["testo"] for p in pezzi).strip():
            righe.append(pezzi)
    return righe


def _testo(pezzi: list[dict[str, Any]]) -> str:
    return "".join(p["testo"] for p in pezzi)


def _linee(pagina: Any) -> int:
    """Quanti segmenti e rettangoli sono disegnati nella pagina (le tabelle ne hanno molti)."""
    try:
        disegni = pagina.get_drawings()
        return sum(1 for d in disegni for item in d.get("items", []) if item[0] in ("l", "re"))
    except Exception:  # noqa: BLE001
        return 0


def _celle(parole: list[tuple], tabella: Any) -> list[list[str]]:
    """Testo delle celle dalle parole della pagina che cadono in ogni riquadro (l'estrazione
    della tabella a volte sposta i punti: «100.000» → «100000 .»). Righe e colonne vuote (celle
    unite) si tolgono."""
    righe = []
    for r in tabella.rows:
        riga = []
        for c in r.cells:
            if not c:
                riga.append("")
                continue
            x0, y0, x1, y1 = c
            dentro = [
                w for w in parole if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1
            ]
            linee: dict[tuple[int, int], list[str]] = {}
            for w in sorted(dentro, key=lambda w: (w[5], w[6], w[7])):
                linee.setdefault((w[5], w[6]), []).append(pulito(w[4]))
            testi = [" ".join(x) for x in linee.values() if "".join(x).strip()]
            riga.append("\n".join(testi))
        if any(riga):
            righe.append(riga)
    if not righe:
        return []
    colonne = max(len(r) for r in righe)
    righe = [r + [""] * (colonne - len(r)) for r in righe]
    piene = [j for j in range(colonne) if any(r[j] for r in righe)]
    return [[r[j] for j in piene] for r in righe]


def _chiave_ripetuta(testo: str) -> str:
    return re.sub(r"\d+", "#", " ".join(testo.lower().split()))


def leggi_pdf(dati: bytes) -> tuple[list[dict[str, Any]], int]:
    """Elementi del documento in ordine: {"tipo": "paragrafo"|"tabella", ...}; e le pagine."""
    import pymupdf

    elementi: list[dict[str, Any]] = []
    scadenza = time.monotonic() + SECONDI_TABELLE
    with pymupdf.open(stream=dati, filetype="pdf") as doc:
        pagine = list(doc)[:MAX_PAGINE]
        grezzi = []  # per pagina: (y, x, tipo, contenuto, bordo)
        bordi: Counter[str] = Counter()
        for p in pagine:
            alto, basso = p.rect.height * 0.09, p.rect.height * 0.91
            voci: list[tuple[float, float, str, Any, bool]] = []
            riquadri = []
            tabelle = []
            # cercare le tabelle è lento (circa un secondo a pagina): solo dove ci sono linee
            # disegnate, ed entro un tempo massimo per documento
            if time.monotonic() < scadenza and _linee(p) >= MIN_LINEE_TABELLA:
                try:
                    tabelle = [
                        t for t in p.find_tables(use_layout=False).tables if t.col_count >= 2
                    ]
                except Exception:  # noqa: BLE001 — una pagina strana: niente tabelle, resta il testo
                    tabelle = []
            parole = p.get_text("words") if tabelle else []
            for t in tabelle:
                righe = _celle(parole, t)
                if righe and len(righe[0]) >= 2:
                    riquadri.append(pymupdf.Rect(t.bbox))
                    voci.append((t.bbox[1], t.bbox[0], "tabella", righe, False))
            for b in p.get_text("dict", sort=True)["blocks"]:
                if b.get("type") != 0:
                    continue
                r = pymupdf.Rect(b["bbox"])
                centro = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                if any(centro in q for q in riquadri):
                    continue
                righe = _righe_blocco(b)
                if not righe:
                    continue
                al_bordo = r.y1 < alto or r.y0 > basso
                if al_bordo:
                    bordi[_chiave_ripetuta(" ".join(_testo(x) for x in righe))] += 1
                voci.append((r.y0, r.x0, "testo", righe, al_bordo))
            grezzi.append(sorted(voci, key=lambda v: (round(v[0]), v[1])))
        n = len(pagine)
        ripetuti = {k for k, c in bordi.items() if n >= 3 and c >= max(3, n // 2)}
        for voci in grezzi:
            for _, _, tipo, contenuto, al_bordo in voci:
                if tipo == "tabella":
                    elementi.append({"tipo": "tabella", "righe": contenuto})
                    continue
                testo = " ".join(_testo(x) for x in contenuto)
                ripetuto = _chiave_ripetuta(testo) in ripetuti or _NUMERO_PAGINA.match(testo)
                if al_bordo and ripetuto:
                    continue
                elementi.extend(_paragrafi(contenuto))
    return _unisci_frasi(elementi), n


def _unisci_frasi(elementi: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una frase che continua nel blocco dopo (o nella pagina dopo) torna un solo paragrafo:
    il precedente non finisce con la punteggiatura e il successivo comincia in minuscolo."""
    uniti: list[dict[str, Any]] = []
    for e in elementi:
        prima = uniti[-1] if uniti else None
        if (
            prima is not None
            and e["tipo"] == prima["tipo"] == "paragrafo"
            and not e["elenco"]
            and _testo(e["pezzi"]).lstrip()[:1].islower()
            and not _testo(prima["pezzi"]).rstrip().endswith((".", ";", ":", "!", "?"))
        ):
            pezzi = [dict(p) for p in e["pezzi"]]
            fine = prima["pezzi"][-1]["testo"].rstrip()
            if fine.endswith("-"):
                prima["pezzi"][-1]["testo"] = fine[:-1]
                pezzi[0]["testo"] = pezzi[0]["testo"].lstrip()
            else:
                prima["pezzi"][-1]["testo"] = fine
                pezzi[0]["testo"] = " " + pezzi[0]["testo"].lstrip()
            prima["pezzi"].extend(pezzi)
            continue
        uniti.append(e)
    return uniti


def _paragrafi(righe: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Righe di un blocco → paragrafi: si riuniscono le righe spezzate, gli elenchi restano
    voci separate."""
    gruppi: list[list[list[dict[str, Any]]]] = []
    for riga in righe:
        if not gruppi or _ELENCO.match(_testo(riga)):
            gruppi.append([riga])
        else:
            gruppi[-1].append(riga)
    paragrafi = []
    for g in gruppi:
        pezzi: list[dict[str, Any]] = []
        for i, riga in enumerate(g):
            riga = [dict(p) for p in riga]
            if i and pezzi:
                fine = pezzi[-1]["testo"].rstrip()
                inizio = _testo(riga).lstrip()
                if fine.endswith("-") and inizio[:1].islower():  # parola spezzata a fine riga
                    pezzi[-1]["testo"] = fine[:-1]
                    riga[0]["testo"] = riga[0]["testo"].lstrip()
                else:
                    pezzi[-1]["testo"] = fine
                    riga[0]["testo"] = " " + riga[0]["testo"].lstrip()
            pezzi.extend(riga)
        testo = " ".join(_testo(pezzi).split())
        if testo:
            paragrafi.append(
                {"tipo": "paragrafo", "pezzi": pezzi, "elenco": bool(_ELENCO.match(testo))}
            )
    return paragrafi


def _corpo_normale(elementi: list[dict[str, Any]]) -> float:
    conta: Counter[float] = Counter()
    for e in elementi:
        for p in e.get("pezzi", []):
            conta[round(p["corpo"], 1)] += len(p["testo"])
    return conta.most_common(1)[0][0] if conta else 11.0


def _livello_titolo(e: dict[str, Any], normale: float) -> int:
    """1 o 2 se il paragrafo è un titolo, 0 altrimenti."""
    pezzi = [p for p in e["pezzi"] if p["testo"].strip()]
    testo = " ".join(_testo(pezzi).split())
    if not pezzi or len(testo) > 160 or e["elenco"]:
        return 0
    lettere = sum(len(p["testo"]) for p in pezzi)
    corpo = sum(p["corpo"] * len(p["testo"]) for p in pezzi) / lettere
    grassetto = all(p["grassetto"] for p in pezzi)
    if corpo >= normale + 2.5:
        return 1
    frase = testo.endswith((".", ";", ",", ":"))
    if corpo >= normale + 1 or (grassetto and not frase and len(testo) < 120):
        return 2
    return 0


# --- scrittura del file Word -----------------------------------------------------------------


def _nuovo_documento(titolo: str) -> Any:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    stile = doc.styles["Normal"]
    stile.font.name = "Calibri"
    stile.font.size = Pt(11)
    stile.paragraph_format.space_after = Pt(6)
    doc.core_properties.title = pulito(titolo)[:250]
    doc.core_properties.author = "Europrojecta Studio"
    return doc


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
LARGHEZZA_TWIP = 9000  # larghezza utile di una pagina A4 con i margini normali


class _Scrittore:
    """Scrive paragrafi e tabelle direttamente nell'XML del documento: con python-docx ogni
    paragrafo nuovo scorre tutti i precedenti, e un bando di 300 pagine richiedeva minuti."""

    def __init__(self, doc: Any) -> None:
        self.doc = doc
        self.fine = doc.element.body.sectPr
        self.stili = {n: doc.styles[f"Heading {n}"].style_id for n in (1, 2)}
        self.griglia = doc.styles["Table Grid"].style_id

    @staticmethod
    def _el(padre: Any, nome: str, **attributi: str) -> Any:
        from lxml import etree

        e = etree.SubElement(padre, _W + nome)
        for k, v in attributi.items():
            e.set(_W + k, v)
        return e

    def _p(self, pezzi: list[dict[str, Any]], titolo: int = 0) -> Any:
        from lxml import etree

        p = etree.Element(_W + "p")
        if titolo:
            self._el(self._el(p, "pPr"), "pStyle", val=self.stili[titolo])
        # pezzi vicini con lo stesso stile in un solo «run»
        uniti: list[list[Any]] = []
        for pezzo in pezzi:
            if not pezzo["testo"]:
                continue
            stile = (bool(pezzo.get("grassetto")), bool(pezzo.get("corsivo")))
            if uniti and uniti[-1][0] == stile:
                uniti[-1][1] += pezzo["testo"]
            else:
                uniti.append([stile, pezzo["testo"]])
        for (grassetto, corsivo), testo in uniti:
            r = self._el(p, "r")
            if grassetto or corsivo:
                rpr = self._el(r, "rPr")
                if grassetto:
                    self._el(rpr, "b")
                if corsivo:
                    self._el(rpr, "i")
            t = self._el(r, "t")
            t.text = testo
            t.set(_XML_SPACE, "preserve")
        return p

    def paragrafo(self, pezzi: list[dict[str, Any]], titolo: int = 0) -> None:
        self.fine.addprevious(self._p(pezzi, titolo))

    def testo(self, testo: str, titolo: int = 0) -> None:
        self.paragrafo([{"testo": testo}], titolo)

    def tabella(self, righe: list[list[str]]) -> None:
        from lxml import etree

        colonne = max(len(r) for r in righe)
        larghezza = str(LARGHEZZA_TWIP // colonne)
        tbl = etree.Element(_W + "tbl")
        pr = self._el(tbl, "tblPr")
        self._el(pr, "tblStyle", val=self.griglia)
        self._el(pr, "tblW", w="5000", type="pct")
        griglia = self._el(tbl, "tblGrid")
        for _ in range(colonne):
            self._el(griglia, "gridCol", w=larghezza)
        for riga in righe:
            tr = self._el(tbl, "tr")
            for j in range(colonne):
                tc = self._el(tr, "tc")
                self._el(self._el(tc, "tcPr"), "tcW", w=larghezza, type="dxa")
                testo = riga[j] if j < len(riga) else ""
                for linea in testo.split("\n"):
                    tc.append(self._p([{"testo": linea}]))
        self.fine.addprevious(tbl)
        self.testo("")


def _da_testo(scrittore: _Scrittore, testo: str) -> None:
    """Testo semplice (scansioni lette con l'OCR): paragrafi separati da righe vuote."""
    for blocco in re.split(r"\n\s*\n", pulito(testo)):
        righe = [r.strip() for r in blocco.splitlines() if r.strip()]
        gruppi: list[list[str]] = []
        for r in righe:
            if not gruppi or _ELENCO.match(r):
                gruppi.append([r])
            else:
                gruppi[-1].append(r)
        for g in gruppi:
            unito = ""
            for r in g:
                if unito.endswith("-") and r[:1].islower():
                    unito = unito[:-1] + r
                else:
                    unito = f"{unito} {r}".strip()
            if unito:
                scrittore.testo(unito)


def in_word(dati: bytes | None, titolo: str, testo_letto: str | None = None) -> bytes:
    """Il file .docx del PDF. `testo_letto` serve quando il PDF non ha testo (scansione) o
    il file non è disponibile."""
    doc = _nuovo_documento(titolo)
    elementi: list[dict[str, Any]] = []
    if dati:
        try:
            elementi, _ = leggi_pdf(dati)
        except Exception:  # noqa: BLE001 — PDF illeggibile: si ripiega sul testo già letto
            elementi = []
    scrittore = _Scrittore(doc)
    lettere = sum(len(_testo(e["pezzi"])) for e in elementi if e["tipo"] == "paragrafo")
    lettere += sum(
        len(c) for e in elementi if e["tipo"] == "tabella" for r in e["righe"] for c in r
    )
    # Scansione (il PDF non ha testo, quello letto viene dall'OCR) o file mancante: testo letto.
    # Un PDF breve ma con testo vero resta com'è, con titoli e tabelle.
    letto = len((testo_letto or "").strip())
    if testo_letto and lettere < 200 and lettere < letto / 2:
        _da_testo(scrittore, testo_letto)
    else:
        _scrivi(scrittore, elementi)
    if not doc.paragraphs and not doc.tables:
        scrittore.testo("Il PDF non contiene testo leggibile.")
    uscita = io.BytesIO()
    doc.save(uscita)
    return uscita.getvalue()


def _scrivi(scrittore: _Scrittore, elementi: list[dict[str, Any]]) -> None:
    normale = _corpo_normale(elementi)
    for e in elementi:
        if e["tipo"] == "tabella":
            scrittore.tabella(e["righe"])
            continue
        livello = _livello_titolo(e, normale)
        if livello:
            scrittore.testo(" ".join(_testo(e["pezzi"]).split()), livello)
        else:  # le voci di elenco tengono il loro segno («a)», «•»)
            scrittore.paragrafo(e["pezzi"])
