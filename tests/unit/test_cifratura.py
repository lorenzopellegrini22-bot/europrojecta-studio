import pytest

from app.sicurezza.cifratura import ErroreCifratura, anteprima, cifra, decifra, impronta


def test_cifra_e_decifra():
    token = cifra("sk-segreto-1234", "prova")
    assert "sk-segreto" not in token
    assert decifra(token, "prova") == "sk-segreto-1234"


def test_scopi_diversi_non_si_decifrano_a_vicenda():
    token = cifra("valore", "scopo-a")
    with pytest.raises(ErroreCifratura):
        decifra(token, "scopo-b")


def test_token_corrotto():
    with pytest.raises(ErroreCifratura):
        decifra("non-un-token", "prova")


def test_impronta_deterministica_e_legata_allo_scopo():
    assert impronta("abc", "x") == impronta("abc", "x")
    assert impronta("abc", "x") != impronta("abc", "y")


def test_anteprima_mostra_solo_ultime_quattro():
    assert anteprima("sk-proj-abcdefgh1234") == "…1234"
    assert anteprima("corta") == "…"
