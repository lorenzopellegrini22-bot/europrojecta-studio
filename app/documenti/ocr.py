"""OCR dei PDF scansionati (pagine fatte di sole immagini).

Le pagine si trasformano in immagini con PyMuPDF e il testo si legge con Tesseract in
italiano (programma `tesseract` con la lingua `ita`, installati nell'immagine Docker).
Si usa solo quando il PDF non ha testo; dove Tesseract manca (es. sviluppo su Windows) la
funzione non fa nulla e il PDF resta «senza testo», come prima.
"""

import logging
import shutil
import subprocess

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
