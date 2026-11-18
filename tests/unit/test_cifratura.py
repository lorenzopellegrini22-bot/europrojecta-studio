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


def test_rotazione_della_chiave(monkeypatch):
    from app.config import get_settings
    from app.sicurezza.cifratura import ricifra

    vecchia = get_settings().secret_key.get_secret_value()
    token = cifra("sk-segreto", "prova")
    try:
        monkeypatch.setenv("SECRET_KEY", "n" * 40)
        monkeypatch.setenv("SECRET_KEY_PRECEDENTI", vecchia)
        get_settings.cache_clear()
        assert decifra(token, "prova") == "sk-segreto"  # la vecchia chiave legge ancora
        nuovo = ricifra(token, "prova")
        monkeypatch.delenv("SECRET_KEY_PRECEDENTI")
        get_settings.cache_clear()
        assert decifra(nuovo, "prova") == "sk-segreto"  # ricifrato con la nuova
        with pytest.raises(ErroreCifratura):
            decifra(token, "prova")  # senza la vecchia il token vecchio non si legge
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
