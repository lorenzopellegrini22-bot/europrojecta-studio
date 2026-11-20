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


def test_apostrofi_e_sigle():
    """Misurato il 20/11/2026: «credito d'imposta» trovava 8 bandi aperti, «credito di
    imposta» 23 (i titoli usano l'apostrofo tipografico); «R&S» mostrava tutti i bandi."""
    assert termini("credito d'imposta") == termini("credito d’imposta") == [["credit"], ["impost"]]
    assert termini("credito d'imposta") == termini("credito di imposta")
    assert termini("dell'innovazione") == [["innovazion"]]
    assert termini("R&S") == termini("r & s") == termini("ricerca e sviluppo")
    assert termini("Smart&Start") == [["smart"], ["start"]]
    assert normalizza("d’imposta") == "d'imposta"
