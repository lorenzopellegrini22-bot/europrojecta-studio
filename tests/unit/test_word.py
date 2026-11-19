"""PDF dei bandi in Word: titoli, paragrafi riuniti, tabelle, intestazioni ripetute tolte."""

import io

import pymupdf
from docx import Document

from app.documenti.word import in_word, nome_file, pulito


def _pdf() -> bytes:
    doc = pymupdf.open()
    for n in range(3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 40), "REGIONE ESEMPIO - Avviso pubblico", fontsize=9)
        p.insert_text((280, 820), f"Pagina {n + 1} di 3", fontsize=9)
        if n == 0:
            p.insert_text((50, 100), "Avviso per le imprese", fontsize=18, fontname="hebo")
            p.insert_text((50, 140), "Art. 1 - Beneficiari", fontsize=11, fontname="hebo")
            y = 170
            for riga in ("Possono partecipare le micro, piccole e medie im-",
                         "prese con sede operativa nel territorio regionale",
                         "che rispettano i requisiti indicati."):  # fmt: skip
                p.insert_text((50, y), riga, fontsize=11)
                y += 14
            p.insert_text((50, 230), "a) iscrizione al Registro delle imprese;", fontsize=11)
            p.insert_text((50, 244), "b) regolarità contributiva.", fontsize=11)
            # tabella 3 x 2 con le linee
            x0, y0, w, h = 50, 300, 200, 20
            for r in range(4):
                p.draw_line((x0, y0 + r * h), (x0 + 2 * w, y0 + r * h))
            for c in range(3):
                p.draw_line((x0 + c * w, y0), (x0 + c * w, y0 + 3 * h))
            for r, (a, b) in enumerate((("Voce", "Importo"), ("Macchinari", "100.000"),
                                        ("Software", "20.000"))):  # fmt: skip
                p.insert_text((x0 + 5, y0 + r * h + 14), a, fontsize=10)
                p.insert_text((x0 + w + 5, y0 + r * h + 14), b, fontsize=10)
        else:
            p.insert_text((50, 100), f"Testo della pagina {n + 1}.", fontsize=11)
    return doc.tobytes()


def test_pdf_in_word():
    d = Document(io.BytesIO(in_word(_pdf(), "Avviso")))
    testi = [p.text for p in d.paragraphs]
    titoli = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    assert "Avviso per le imprese" in titoli and "Art. 1 - Beneficiari" in titoli
    # righe spezzate riunite, parola divisa dal trattino ricomposta
    assert any(
        "piccole e medie imprese con sede operativa nel territorio regionale che rispettano" in t
        for t in testi
    )
    # le voci di elenco restano separate
    assert "a) iscrizione al Registro delle imprese;" in testi
    assert "b) regolarità contributiva." in testi
    # intestazione ripetuta e numeri di pagina tolti
    assert not any("REGIONE ESEMPIO" in t or "Pagina 1 di 3" in t for t in testi)
    assert "Testo della pagina 3." in testi
    # tabella vera, non testo sparso
    assert len(d.tables) == 1
    righe = [[c.text for c in r.cells] for r in d.tables[0].rows]
    assert righe == [["Voce", "Importo"], ["Macchinari", "100.000"], ["Software", "20.000"]]
    assert d.core_properties.title == "Avviso"


def test_scansione_usa_il_testo_letto():
    vuoto = pymupdf.open()
    vuoto.new_page()
    letto = "Bando per le imprese\n\nIl contributo è con-\ncesso a fondo perduto.\n\x0c"
    d = Document(io.BytesIO(in_word(vuoto.tobytes(), "Scansione", letto)))
    assert [p.text for p in d.paragraphs] == [
        "Bando per le imprese",
        "Il contributo è concesso a fondo perduto.",
    ]


def test_file_mancante_e_pdf_rotto():
    d = Document(io.BytesIO(in_word(None, "Senza file", "Solo testo letto.")))
    assert d.paragraphs[0].text == "Solo testo letto."
    d = Document(io.BytesIO(in_word(b"%PDF-1.4 rotto", "Rotto", None)))
    assert "non contiene testo" in d.paragraphs[0].text


def test_nome_file_e_caratteri():
    assert nome_file("Allegato 4 - Domanda di rimborso ( pdf 555.84 KB)") == (
        "Allegato 4 - Domanda di rimborso.docx"
    )
    assert nome_file("Bando.PDF") == "Bando.docx"
    assert nome_file(None) == "documento.docx"
    assert nome_file('a/b:"c"') == "abc.docx"
    assert pulito("a\x00b\x0cc\ufffd") == "abc"


def test_frase_che_continua_nella_pagina_dopo():
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 700), "Il contributo spetta alle imprese che", fontsize=11)
    doc.new_page().insert_text((50, 100), "hanno sede nel territorio.", fontsize=11)
    d = Document(io.BytesIO(in_word(doc.tobytes(), "Frase")))
    assert [p.text for p in d.paragraphs] == [
        "Il contributo spetta alle imprese che hanno sede nel territorio."
    ]


def test_pdf_breve_tiene_titoli_e_tabelle():
    """Un PDF di poche righe con testo vero non ripiega sul testo semplice già letto."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((50, 80), "Avviso breve", fontsize=18, fontname="hebo")
    p.insert_text((50, 120), "Domande entro il 30 giugno.", fontsize=11)
    d = Document(
        io.BytesIO(in_word(doc.tobytes(), "Breve", "Avviso breve\nDomande entro il 30 giugno."))
    )
    assert d.paragraphs[0].style.name.startswith("Heading")
