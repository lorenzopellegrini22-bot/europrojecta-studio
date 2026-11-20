"""Fonte Invitalia: schede dell'elenco, stati, date, territorio dal nome, esclusioni.

L'HTML è costruito qui con la stessa struttura delle pagine elenco (verifica del 20/11/2026),
senza copiare i testi del sito."""

from datetime import date

from app.sources import invitalia


def _scheda(slug: str, titolo: str, stato: str, apertura: str = "", chiusura: str = "") -> str:
    date_html = ""
    if apertura:
        date_html += (
            f'<span class="apertura">Data apertura: <span><strong>{apertura}</strong></span></span>'
        )
    if chiusura:
        date_html += (
            f'<span class="chiusura">Data chiusura: <span><strong>{chiusura}</strong></span></span>'
        )
    return f"""
<div class="card-body position-relative">
  <a class="read-more" href="/incentivi-e-strumenti/{slug}"><span>Leggi tutto</span></a>
  <div class="category-top {stato.lower()}"><span class="category">{stato}</span></div>
  <h3><a class="card-unified__title" href="/incentivi-e-strumenti/{slug}">{titolo}</a></h3>
  <p class="fw-normal">Presentazione di prova di {titolo}</p>
  <p class="dateContainer card-text">
    <span class="category {stato.lower()}">{stato}</span>{date_html}
  </p>
</div>"""


PAGINA = (
    "<html><body><main>"
    + _scheda("voucher-prova", "Voucher di prova", "Attivo", "20 ottobre 2026", "20 gennaio 2027")
    + _scheda("brevetti-prova", "Brevetti+ prova", "In apertura", "25 novembre 2026")
    + _scheda("resto-al-sud-prova", "Resto al Sud 2.0", "Attivo", "15 ottobre 2025")
    + _scheda("autoimpiego-prova", "Autoimpiego Centro-Nord", "Attivo")
    + _scheda("vecchio", "Incentivo chiuso", "Chiuso", "1 marzo 2024", "30 aprile 2024")
    + _scheda("albi", "Albi fornitori Invitalia", "Attivo")
    + '<nav><a href="?page=0">1</a><a href="?page=1">2</a><a href="?page=4">5</a></nav>'
    + "</main></body></html>"
)


def test_schede_dell_elenco():
    schede = invitalia.schede(PAGINA)
    assert [s["slug"] for s in schede][:3] == [
        "voucher-prova",
        "brevetti-prova",
        "resto-al-sud-prova",
    ]
    assert schede[0]["stato"] == "attivo" and schede[1]["stato"] == "in apertura"
    assert schede[0]["presentazione"] == "Presentazione di prova di Voucher di prova"
    assert invitalia.ultima_pagina(PAGINA) == 4


def test_record():
    per_slug = {s["slug"]: invitalia.record(s) for s in invitalia.schede(PAGINA)}
    v = per_slug["voucher-prova"]
    assert (v.data_apertura, v.data_chiusura, v.stato_fonte) == (
        date(2026, 10, 20),
        date(2027, 1, 20),
        "aperto",
    )
    assert v.link_ufficiale == "https://www.invitalia.it/incentivi-e-strumenti/voucher-prova"
    assert v.ente == "Invitalia" and v.requisiti.territorio == "nazionale"
    assert "territorio" in v.requisiti.da_verificare
    assert per_slug["brevetti-prova"].stato_fonte == "in_arrivo"
    assert per_slug["vecchio"].stato_fonte == "chiuso"
    # misure riservate a una parte d'Italia, dal nome
    sud = per_slug["resto-al-sud-prova"].requisiti
    assert (
        sud.territorio == "regionale" and "Campania" in sud.regioni and "Lazio" not in sud.regioni
    )
    cn = per_slug["autoimpiego-prova"].requisiti
    assert "Lazio" in cn.regioni and "Campania" not in cn.regioni
    assert per_slug["autoimpiego-prova"].note_apertura_chiusura  # chiusura non indicata


def test_esclusi_i_servizi_di_invitalia(tmp_path):
    from app.sources.regioni import comune

    schede = {s["slug"]: s for s in invitalia.schede(PAGINA)}
    percorso = comune.salva_pacchetto({"schede": schede})
    titoli = [r.titolo for r in invitalia.FONTE.interpreta(percorso)]
    assert "Albi fornitori Invitalia" not in titoli and "Voucher di prova" in titoli
