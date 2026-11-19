# ruff: noqa: E501
"""PDF trovati nella pagina ufficiale del bando: quelli del bando sì, quelli del sito no."""

from app.documenti.pagina import pdf_della_pagina

PAGINA = """
<html><body>
<header><a href="/privacy.pdf">Privacy</a></header>
<nav><a href="/guida-sito.pdf">Guida al sito</a></nav>
<main>
  <h1>Avviso caro carburante</h1>
  <a href="/uploads/Atto_18546.pdf">Decreto n. 18546 - Approvazione Avviso</a>
  <a href="/uploads/Faq_5.pdf">FAQ n. 5</a>
  <a href="/uploads/Allegato_A_Bando.pdf">Ascolta</a>
  <a href="/uploads/Allegato_A_Bando.pdf">Allegato A - Bando</a>
  <a href="/uploads/modulo-rendicontazione.pdf">Modulo di rendicontazione</a>
  <a href="/note-legali/Note_legali_it.pdf">Note legali</a>
  <a href="/pagina.html">Altra pagina</a>
</main>
<footer><a href="/cookie_policy.pdf">Cookie policy</a></footer>
</body></html>
"""


def test_pdf_utili_dalla_pagina():
    trovati = pdf_della_pagina(PAGINA, "https://ente.example/bandi/carburante")
    url = [d["url"] for d in trovati]
    assert url[0] == "https://ente.example/uploads/Allegato_A_Bando.pdf"
    assert set(url) == {
        "https://ente.example/uploads/Allegato_A_Bando.pdf",
        "https://ente.example/uploads/Atto_18546.pdf",
        "https://ente.example/uploads/Faq_5.pdf",
    }
    # «Ascolta» non è un titolo: vale quello del link vero allo stesso file
    assert trovati[0]["titolo"] == "Allegato A - Bando"


def test_pagina_senza_pdf():
    assert (
        pdf_della_pagina("<html><body><p>Nessun file</p></body></html>", "https://e.example/") == []
    )


def test_codifica_della_pagina():
    from app.sources.http import decodifica

    testo = "Voucher Turismo – Anno 2026, città"
    assert decodifica(testo.encode("cp1252")) == testo  # non dichiarata, non UTF-8
    assert decodifica(testo.encode("utf-8")) == testo
    html = '<meta charset="iso-8859-1"><p>città</p>'.encode("latin-1")
    assert "città" in decodifica(html)
    assert decodifica("à".encode(), "utf-8") == "à"


def test_titoli_senza_dimensione():
    from app.documenti.documentazione import pulisci

    assert pulisci("Allegato A - Modulo di domanda -319 KB") == "Allegato A - Modulo di domanda"
    assert pulisci("Allegato 4 ( pdf 199.31 KB)") == "Allegato 4"
    assert pulisci("Bando 2026 – 1,2 MB") == "Bando 2026"
