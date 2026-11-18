from app.auth.password import hash_password, valida_nuova_password, verifica_password


def test_hash_e_verifica():
    h = hash_password("una-password-lunga")
    assert h.startswith("$argon2id$")
    assert verifica_password(h, "una-password-lunga")
    assert not verifica_password(h, "sbagliata")


def test_utente_inesistente_non_passa_mai():
    assert not verifica_password(None, "password-fittizia-per-tempi-costanti")


def test_lunghezza_minima():
    assert valida_nuova_password("corta") is not None
    assert valida_nuova_password("abbastanza-lunga") is None
