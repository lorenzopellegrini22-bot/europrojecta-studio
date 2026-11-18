"""OCR dei PDF scansionati: si crea un PDF fatto di sola immagine e lo si legge.
Serve Tesseract con l'italiano (in CI e nell'immagine Docker); altrimenti il test è saltato."""

import pytest

from app.documenti import ocr
from app.documenti import (
    servizio as documenti,  # (testo_pdf non va importato: pytest lo raccoglierebbe)
)


def _pdf_scansionato() -> bytes:
    import pymupdf

    righe = [
        "Bando per la digitalizzazione delle piccole e medie imprese.",
        "Contributo a fondo perduto pari al cinquanta per cento delle spese ammissibili.",
        "Le domande si presentano entro il trentuno gennaio duemilaventisette.",
    ] * 3
    sorgente = pymupdf.open()
    pagina = sorgente.new_page()
    pagina.insert_text((50, 80), "\n".join(righe), fontsize=12)
    immagine = pagina.get_pixmap(dpi=200).tobytes("png")
    scansione = pymupdf.open()
    nuova = scansione.new_page()
    nuova.insert_image(nuova.rect, stream=immagine)  # solo immagine, nessun testo
    return scansione.tobytes()


@pytest.mark.skipif(not ocr.disponibile(), reason="Tesseract non installato")
def test_pdf_scansionato_letto_con_ocr():
    testo, pagine = documenti.testo_pdf(_pdf_scansionato())
    assert pagine == 1 and testo.startswith(ocr.SEGNO)
    minuscolo = testo.lower()
    assert "digitalizzazione" in minuscolo and "fondo perduto" in minuscolo


def test_senza_tesseract_nessun_errore(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda _nome: None)
    assert ocr.ocr_pdf(_pdf_scansionato()) == ""
    testo, _ = documenti.testo_pdf(_pdf_scansionato())
    assert testo == ""
