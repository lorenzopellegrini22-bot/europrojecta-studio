"""PDF scansionati trascritti dall'AI dove Tesseract non c'è (Vercel)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.documenti import ocr
from app.documenti import servizio as documenti
from app.models import UsoLLM
from tests.integration.test_estrazione_ai import ai_pronta  # noqa: F401 — fixture
from tests.unit.test_ocr import _pdf_scansionato

pytestmark = pytest.mark.integration

TRASCRITTO = (
    "Bando per la digitalizzazione delle piccole e medie imprese. Contributo a fondo "
    "perduto pari al cinquanta per cento delle spese ammissibili. Le domande si presentano "
    "entro il trentuno gennaio duemilaventisette sulla piattaforma regionale."
)


class OpenAIFinto:
    def __init__(self, **_kw):
        self.chiamate = []
        self.responses = self

    def create(self, **kw):
        self.chiamate.append(kw)
        return SimpleNamespace(
            output_text=TRASCRITTO, usage=SimpleNamespace(input_tokens=1500, output_tokens=120)
        )


@pytest.fixture
def senza_tesseract(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda _nome: None)


def test_pdf_scansionato_trascritto_dall_ai(db, ai_pronta, senza_tesseract, monkeypatch):  # noqa: F811
    finto = OpenAIFinto()
    monkeypatch.setattr("openai.OpenAI", lambda **_kw: finto)
    quota = ocr.QuotaOCR(1)

    testo, pagine = documenti.testo_pdf(_pdf_scansionato(), db, quota)
    db.commit()

    assert pagine == 1 and testo.startswith(ocr.SEGNO_AI) and "fondo perduto" in testo
    chiamata = finto.chiamate[0]
    immagini = [c for c in chiamata["input"][0]["content"] if c["type"] == "input_image"]
    assert len(immagini) == 1 and immagini[0]["image_url"].startswith("data:image/png;base64,")
    uso = db.scalar(select(UsoLLM).where(UsoLLM.operazione == "ocr"))
    assert uso is not None and uso.esito == "ok" and uso.token_ingresso == 1500
    assert quota.rimasti == 0

    # Quota della notte esaurita: niente nuove chiamate, il PDF resta senza testo
    testo, _ = documenti.testo_pdf(_pdf_scansionato(), db, quota)
    assert testo == "" and len(finto.chiamate) == 1


def test_senza_openai_nessuna_trascrizione(db, senza_tesseract, monkeypatch):
    from app.models import Integrazione

    db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
    db.commit()
    monkeypatch.setattr("openai.OpenAI", lambda **_kw: pytest.fail("non deve chiamare OpenAI"))
    testo, _ = documenti.testo_pdf(_pdf_scansionato(), db)
    assert testo == ""
