from datetime import UTC, datetime, timedelta

import pyotp

from app.auth import totp

ADESSO = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)


def test_codice_corrente_accettato_e_non_riutilizzabile():
    segreto = totp.nuovo_segreto()
    codice = pyotp.TOTP(segreto).at(ADESSO)
    intervallo = totp.verifica_codice(segreto, codice, None, ADESSO)
    assert intervallo is not None
    assert totp.verifica_codice(segreto, codice, intervallo, ADESSO) is None


def test_tolleranza_un_intervallo():
    segreto = totp.nuovo_segreto()
    precedente = pyotp.TOTP(segreto).at(ADESSO - timedelta(seconds=30))
    assert totp.verifica_codice(segreto, precedente, None, ADESSO) is not None
    vecchio = pyotp.TOTP(segreto).at(ADESSO - timedelta(seconds=120))
    assert totp.verifica_codice(segreto, vecchio, None, ADESSO) is None


def test_codice_malformato():
    segreto = totp.nuovo_segreto()
    assert totp.verifica_codice(segreto, "12", None, ADESSO) is None
    assert totp.verifica_codice(segreto, "", None, ADESSO) is None


def test_codici_recupero():
    codici = totp.genera_codici_recupero()
    assert len(codici) == 10
    assert len(set(codici)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codici)
    # La normalizzazione accetta maiuscole e assenza del trattino
    c = codici[0]
    assert totp.hash_codice_recupero(c.upper().replace("-", "")) == totp.hash_codice_recupero(c)


def test_qr_svg_inline():
    svg = totp.qr_svg(totp.uri_provisioning(totp.nuovo_segreto(), "a@b.it"))
    assert svg.startswith("<svg")
