"""OCR dei PDF scansionati (pagine fatte di sole immagini).

Le pagine si trasformano in immagini con PyMuPDF e il testo si legge con Tesseract in
italiano (programma `tesseract` con la lingua `ita`, installati nell'immagine Docker).
Si usa solo quando il PDF non ha testo; dove Tesseract manca (es. sviluppo su Windows) la
funzione non fa nulla e il PDF resta «senza testo», come prima.
"""

import logging
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

MAX_PAGINE_OCR = 20
DPI = 250
TIMEOUT_PAGINA = 90
SEGNO = "[Testo letto con OCR da un PDF scansionato: possibili errori di lettura]"


def disponibile() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_pdf(dati: bytes, lingua: str = "ita", max_pagine: int = MAX_PAGINE_OCR) -> str:
    """Testo delle prime pagine del PDF letto con OCR ("" se non disponibile o in errore)."""
    if not disponibile():
        return ""
    import pymupdf

    testi = []
    try:
        with pymupdf.open(stream=dati, filetype="pdf") as doc:
            for pagina in list(doc)[:max_pagine]:
                immagine = pagina.get_pixmap(dpi=DPI).tobytes("png")
                r = subprocess.run(  # noqa: S603 — comando fisso, immagine passata su stdin
                    ["tesseract", "stdin", "stdout", "-l", lingua],
                    input=immagine,
                    capture_output=True,
                    timeout=TIMEOUT_PAGINA,
                    check=False,
                )
                if r.returncode == 0:
                    testi.append(r.stdout.decode("utf-8", "replace"))
                else:
                    log.warning("OCR non riuscito su una pagina: %s", r.stderr[:200])
    except Exception as e:  # noqa: BLE001 — l'OCR è un aiuto: in caso di errore si va avanti
        log.warning("OCR non riuscito: %s", type(e).__name__)
        return ""
    testo = "\n".join(t.strip() for t in testi if t.strip())
    return f"{SEGNO}\n{testo}" if testo else ""


# --- OCR con l'AI (dove Tesseract manca, es. Vercel) -------------------------------------

SEGNO_AI = "[Testo trascritto dall'AI da un PDF scansionato: possibili errori di lettura]"
MAX_PAGINE_AI = 10
DPI_AI = 150
ISTRUZIONI_AI = (
    "Sono le pagine scansionate di un bando pubblico italiano, in ordine. Trascrivi "
    "fedelmente tutto il testo, senza riassumere né commentare. Le tabelle riga per riga, "
    "con le celle separate da « | ». Se una parte è illeggibile scrivi [illeggibile]."
)


class QuotaOCR:
    """Quante trascrizioni con l'AI si possono ancora fare (None = senza limite)."""

    def __init__(self, rimasti: int | None = None) -> None:
        self.rimasti = rimasti

    def disponibile(self) -> bool:
        return self.rimasti is None or self.rimasti > 0

    def usa(self) -> None:
        if self.rimasti is not None:
            self.rimasti -= 1


def ocr_ai(dati: bytes, db: "Session", quota: QuotaOCR | None = None, client: Any = None) -> str:
    """Trascrive con il modello OpenAI dello studio le prime pagine di un PDF scansionato.
    "" se OpenAI non è configurato, il budget del mese è finito o la quota è esaurita.
    Il costo si registra in uso_llm come ogni altra chiamata."""
    from app.extraction.servizio import configurazione, registra_uso, stato_budget

    if quota is not None and not quota.disponibile():
        return ""
    conf, _motivo = configurazione(db)
    if conf is None:
        return ""
    budget = stato_budget(db)
    if budget["superato"] or budget["sospesa"]:
        return ""
    try:
        import base64

        import pymupdf

        with pymupdf.open(stream=dati, filetype="pdf") as doc:
            immagini = [
                base64.b64encode(p.get_pixmap(dpi=DPI_AI).tobytes("png")).decode()
                for p in list(doc)[:MAX_PAGINE_AI]
            ]
    except Exception as e:  # noqa: BLE001 — PDF non apribile: si resta senza testo
        log.warning("OCR con l'AI: PDF non apribile (%s)", type(e).__name__)
        return ""
    if not immagini:
        return ""
    if quota is not None:
        quota.usa()
    if client is None:
        import openai

        client = openai.OpenAI(api_key=conf.api_key, timeout=120.0, max_retries=1)
    contenuto = [{"type": "input_text", "text": f"Pagine: {len(immagini)}."}] + [
        {"type": "input_image", "image_url": f"data:image/png;base64,{b}"} for b in immagini
    ]
    try:
        risposta = client.responses.create(
            model=conf.modello,
            instructions=ISTRUZIONI_AI,
            input=[{"role": "user", "content": contenuto}],
        )
    except Exception as e:  # noqa: BLE001 — l'OCR è un aiuto: si registra e si va avanti
        registra_uso(db, "ocr", conf.modello, 0, 0, "errore", type(e).__name__)
        log.warning("OCR con l'AI non riuscito: %s", type(e).__name__)
        return ""
    uso = getattr(risposta, "usage", None)
    registra_uso(
        db,
        "ocr",
        conf.modello,
        getattr(uso, "input_tokens", 0) or 0,
        getattr(uso, "output_tokens", 0) or 0,
        "ok",
    )
    testo = (getattr(risposta, "output_text", "") or "").strip()
    return f"{SEGNO_AI}\n{testo}" if testo else ""
