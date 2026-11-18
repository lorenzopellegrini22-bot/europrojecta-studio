"""PDF dei bandi: scelta dei file, robots.txt, scarico, testo, brani per l'AI."""

from types import SimpleNamespace

import httpx
import pytest

from app.documenti import servizio as s


def pdf_con_testo(righe: list[str]) -> bytes:
    """Un PDF minimo e valido con del testo (font standard Helvetica)."""
    contenuto = (
        "BT /F1 11 Tf 50 780 Td 14 TL "
        + " ".join("(" + r.replace("(", "").replace(")", "") + ") '" for r in righe)
        + " ET"
    )
    oggetti = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(contenuto)} >>\nstream\n{contenuto}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    posizioni = []
    for i, o in enumerate(oggetti, start=1):
        posizioni.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(oggetti) + 1}\n0000000000 65535 f \n".encode()
    for p in posizioni:
        out += f"{p:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(oggetti) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return out


TESTO_BANDO = [
    "Art. 3 Soggetti beneficiari: micro, piccole e medie imprese con sede in Toscana.",
    "Art. 5 Spese ammissibili: acquisto di macchinari e software.",
    "Art. 7 Agevolazione: contributo a fondo perduto del 40 per cento in regime de minimis.",
    "Art. 9 Termini: le domande si presentano dal 1 dicembre 2026 al 31 gennaio 2027.",
] * 3


def test_testo_pdf():
    testo, pagine = s.testo_pdf(pdf_con_testo(TESTO_BANDO))
    assert pagine == 1 and "Soggetti beneficiari" in testo and "de minimis" in testo
    with pytest.raises(s.ErroreDocumento):
        s.testo_pdf(b"%PDF-1.4 rotto")


def test_scelta_dei_pdf():
    b = SimpleNamespace(
        link_ufficiale="https://ente.it/pagina.html",
        link_domande=None,
        raw={
            "documenti_pdf": [
                {"titolo": "Contatti banche", "url": "https://ente.it/Contatti-BNL.pdf"},
                {"titolo": "DD 123 - Bando", "url": "https://ente.it/DD_123_Bando.pdf"},
                {"titolo": "Modulistica", "url": "https://ente.it/Modulistica.pdf"},
                {"titolo": "FAQ", "url": "https://ente.it/faq.pdf"},
            ],
            "Base_normativa": "vedi https://ente.it/decreto-attuativo.pdf",
        },
    )
    scelti = [c["url"].rsplit("/", 1)[-1] for c in s.candidati(b)]
    assert scelti[0] == "DD_123_Bando.pdf"
    assert "Contatti-BNL.pdf" not in scelti and "Modulistica.pdf" not in scelti
    assert len(scelti) <= s.MAX_PER_BANDO and "decreto-attuativo.pdf" in scelti


def test_brani_utili():
    paragrafi = ["Premessa generica sul contesto economico. " * 20] * 10 + [
        "Spese ammissibili: macchinari. Beneficiari: PMI. Regime de minimis."
    ]
    testo = "\n\n".join(paragrafi)
    breve = s.brani_utili(testo, 300)
    assert "Spese ammissibili" in breve and len(breve) <= 300
    assert s.brani_utili("corto", 1000) == "corto"


def test_robots_e_scarico(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cartella_allegati", str(tmp_path / "allegati"))
    monkeypatch.setattr(s.http, "INTERVALLO_MINIMO_SECONDI", 0)
    pdf = pdf_con_testo(TESTO_BANDO)

    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /riservato/\n")
        if req.url.path == "/riservato/bando.pdf":
            return httpx.Response(200, content=pdf)
        if req.url.path == "/pagina.pdf":
            return httpx.Response(200, text="<html>non è un pdf</html>")
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        robots = s.Robots(c)
        assert robots.permesso("https://ente.it/bando.pdf")
        assert not robots.permesso("https://ente.it/riservato/bando.pdf")
        assert s.scarica_pdf(c, "https://ente.it/bando.pdf").startswith(b"%PDF")
        with pytest.raises(s.ErroreDocumento, match="non porta a un PDF"):
            s.scarica_pdf(c, "https://ente.it/pagina.pdf")
