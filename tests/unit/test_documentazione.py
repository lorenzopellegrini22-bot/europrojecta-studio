# ruff: noqa: E501
"""Sezione «Documentazione»: link con il nome del tipo di documento, come nelle schede gara."""

from types import SimpleNamespace

from app.documenti.documentazione import pulisci, voci, voci_bando


def test_etichette_per_tipo():
    elenco = voci(
        [
            {"titolo": "Disciplinare di gara.pdf", "url": "https://e.example/2"},
            {"titolo": "Bando di gara", "url": "https://e.example/1"},
            {"titolo": "Domanda di partecipazione.docx", "url": "https://e.example/3"},
            {"titolo": "Allegato_A_modello.doc", "url": "https://e.example/4"},
            {"titolo": "Allegato_B_dichiarazione.doc", "url": "https://e.example/5"},
            {"titolo": "senza link", "url": ""},
        ]
    )
    assert [v["etichetta"] for v in elenco] == [
        "Bando di gara",
        "Disciplinare",
        "Domanda di partecipazione",
        "Allegato A modello",
        "Allegato B dichiarazione",
    ]


def test_bando_con_pdf_caricato_e_pagina_ufficiale():
    bando = SimpleNamespace(link_ufficiale="https://ente.example/bando", link_domande=None)
    docs = [
        SimpleNamespace(id=7, bando_id=3, url="caricato-a-mano:ab", percorso="2026/x.pdf", titolo="Bando.pdf"),
        SimpleNamespace(id=8, bando_id=3, url="caricato-a-mano:cd", percorso=None, titolo="Rotto"),
    ]  # fmt: skip
    elenco = voci_bando(bando, docs)
    assert [(v["etichetta"], v["url"]) for v in elenco] == [
        ("Bando", "/bandi/3/documenti/7"),
        ("Pagina ufficiale del bando", "https://ente.example/bando"),
    ]


def test_pulisci_titoli_dei_siti():
    assert (
        pulisci("Decreto n.95 (PDF — 1,1 MB - 07.05.2026) (si apre in nuova finestra)")
        == "Decreto n.95"
    )
    assert pulisci("All. 4 DUVRI (1).pdf.p7m") == "All. 4 DUVRI"
