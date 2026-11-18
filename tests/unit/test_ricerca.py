"""Ricerca testuale: parole in qualsiasi ordine, accenti, plurali, sinonimi, regioni."""

from app.utils.ricerca import normalizza, separa_regione, termini


def test_termini():
    assert termini("transizione ecologica per le imprese") == [
        ["transizion"],
        ["ecologic"],
        ["impres"],
    ]
    assert termini("attività") == termini("attivita") == [["attivit"]]
    assert termini("fotovoltaico")[0] == ["fotovoltaic", "solare"]
    assert termini("Start-Up innovative")[0] == ["startup", "start up", "start-up"]
    assert termini("bando di gara") == []
    assert termini("") == []


def test_separa_regione():
    assert separa_regione("digitalizzazione Toscana") == ("Toscana", "digitalizzazione")
    assert separa_regione("turismo valle d'aosta")[0] == "Valle d'Aosta/Vallée d'Aoste"
    assert separa_regione("emilia-romagna energia")[0] == "Emilia-Romagna"
    assert separa_regione("lazio") == ("Lazio", "")
    assert separa_regione("palazzo storico") == (None, "palazzo storico")


def test_normalizza():
    assert normalizza("Società PERCHÉ") == "societa perche"


def test_sinonimi_del_settore():
    from app.utils.ricerca import termini

    gruppi = termini("digitalizzazione")
    assert any("digital" in g for g in gruppi)
    assert any("export" in g for g in termini("internazionalizzazione imprese"))
