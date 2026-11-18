"""Funzioni pure delle pratiche: partita IVA, testi delle comunicazioni, righe, spostamenti."""

from types import SimpleNamespace

from app.pratiche import servizio as s


def test_partita_iva():
    assert s.partita_iva_valida("01234567897")
    assert not s.partita_iva_valida("01234567890")
    assert not s.partita_iva_valida("0123456789")
    assert not s.partita_iva_valida("0123456789A")


def test_componi_segnaposto():
    valori = {"referente": "Maria", "bando": "Voucher"}
    assert s.componi("Gentile {referente}, per {bando}", valori) == "Gentile Maria, per Voucher"
    # segnaposto sconosciuto: resta visibile per essere corretto
    assert s.componi("Ciao {nome}", valori) == "Ciao {nome}"
    # graffe non bilanciate: il testo resta com'è
    assert s.componi("Prezzo {", valori) == "Prezzo {"
    assert s.componi("Attributo {referente.x}", valori) == "Attributo {referente.x}"
    assert s.componi(None, valori) == ""


def test_righe():
    assert s.righe("  a \n\n b\n") == ["a", "b"]
    assert s.righe("  \n ") is None
    assert s.righe(None) is None


def test_sposta_e_rinumera():
    a, b, c = (SimpleNamespace(numero=n) for n in (1, 2, 3))
    elementi = [a, b, c]
    s.sposta(elementi, c, -1)
    assert (a.numero, b.numero, c.numero) == (1, 3, 2)
    s.sposta(elementi, a, -1)  # già primo: niente
    assert a.numero == 1
    elementi.remove(b)
    s.rinumera(elementi)
    assert (a.numero, c.numero) == (1, 2)
