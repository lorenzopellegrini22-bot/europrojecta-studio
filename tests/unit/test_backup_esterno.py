import contextlib
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app import backup_esterno as b
from app.config import get_settings

CHIAVE = "AKIAIOSFODNN7EXAMPLE"
SEGRETO = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
ORA_AWS = datetime(2013, 5, 24, tzinfo=UTC)


def _firma(intestazioni: dict[str, str]) -> str:
    return intestazioni["authorization"].rsplit("Signature=", 1)[1]


def test_firma_come_esempio_aws_get_object():
    h = b.firma_sigv4(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        {"Range": "bytes=0-9"},
        b.HASH_VUOTO,
        CHIAVE,
        SEGRETO,
        "us-east-1",
        ORA_AWS,
    )
    assert _firma(h) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["authorization"]


def test_firma_come_esempio_aws_list_objects():
    h = b.firma_sigv4(
        "GET",
        "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J",
        {},
        b.HASH_VUOTO,
        CHIAVE,
        SEGRETO,
        "us-east-1",
        ORA_AWS,
    )
    assert _firma(h) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


def test_cifra_e_decifra_a_blocchi(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(b, "BLOCCO", 1000)
    chiaro = tmp_path / "export.zip"
    chiaro.write_bytes(bytes(range(256)) * 20)  # 5 blocchi
    cifrato, aperto = tmp_path / "x.cifrato", tmp_path / "aperto.zip"
    sha = b.cifra_file(chiaro, cifrato, "passphrase lunga di prova")
    assert len(sha) == 64 and chiaro.read_bytes() not in cifrato.read_bytes()
    b.decifra_file(cifrato, aperto, "passphrase lunga di prova")
    assert aperto.read_bytes() == chiaro.read_bytes()

    with pytest.raises(b.ErroreBackup, match="Passphrase sbagliata"):
        b.decifra_file(cifrato, aperto, "sbagliata")
    troncato = tmp_path / "troncato"
    dati = cifrato.read_bytes()
    # 5120 byte = 5 blocchi da 1000 + uno da 120: l'ultimo record è 4 + 120 + 16 byte
    troncato.write_bytes(dati[: len(dati) - 140])
    with pytest.raises(b.ErroreBackup, match="incompleto"):
        b.decifra_file(troncato, aperto, "passphrase lunga di prova")
    alterato = tmp_path / "alterato"
    alterato.write_bytes(dati[:60] + bytes([dati[60] ^ 1]) + dati[61:])
    with pytest.raises(b.ErroreBackup):
        b.decifra_file(alterato, aperto, "passphrase lunga di prova")
    with pytest.raises(b.ErroreBackup, match="Non è un backup"):
        b.decifra_file(chiaro, aperto, "passphrase lunga di prova")


def test_esegui_carica_cifrato_e_cancella_i_vecchi(tmp_path: Path, monkeypatch):
    s = get_settings()
    for campo, valore in {
        "backup_passphrase": SecretStr("passphrase lunga di prova"),
        "backup_s3_endpoint": "https://storage.test",
        "backup_s3_bucket": "studio",
        "backup_s3_chiave": "chiave",
        "backup_s3_segreto": SecretStr("segreto"),
        "backup_giorni": 30,
    }.items():
        monkeypatch.setattr(s, campo, valore)

    def finto_zip(_db, destinazione: Path) -> dict[str, int]:
        with zipfile.ZipFile(destinazione, "w") as z:
            z.writestr("dati/azienda.csv", "id;ragione_sociale\n1;Prova\n")
        return {"azienda": 1, "file_allegati": 0}

    monkeypatch.setattr("app.esportazione.crea_zip", finto_zip)
    monkeypatch.setattr("app.db.get_sessionmaker", lambda: lambda: contextlib.nullcontext())

    caricati: dict[str, bytes] = {}
    eliminati: list[str] = []

    def gestisci(richiesta: httpx.Request) -> httpx.Response:
        assert richiesta.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=chiave/")
        percorso = richiesta.url.path
        if richiesta.method == "PUT":
            caricati[percorso] = richiesta.read()
            return httpx.Response(200)
        if richiesta.method == "GET":
            assert richiesta.url.params["prefix"] == "europrojecta-backup/"
            chiavi = [
                "europrojecta-backup/2026-09-01.zip.cifrato",
                "europrojecta-backup/2026-11-01.zip.cifrato",
                "europrojecta-backup/2026-11-19.zip.cifrato",
                "europrojecta-backup/note.txt",
            ]
            xml = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in chiavi)
            return httpx.Response(
                200,
                content=(
                    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"{xml}<IsTruncated>false</IsTruncated></ListBucketResult>"
                ).encode(),
            )
        if richiesta.method == "DELETE":
            eliminati.append(percorso)
            return httpx.Response(204)
        return httpx.Response(405)

    client = httpx.Client(transport=httpx.MockTransport(gestisci))
    messaggio = b.esegui(client, oggi=date(2026, 11, 19))

    assert list(caricati) == ["/studio/europrojecta-backup/2026-11-19.zip.cifrato"]
    cifrato, aperto = tmp_path / "scaricato", tmp_path / "aperto.zip"
    cifrato.write_bytes(caricati["/studio/europrojecta-backup/2026-11-19.zip.cifrato"])
    b.decifra_file(cifrato, aperto, "passphrase lunga di prova")
    with zipfile.ZipFile(aperto) as z:
        assert "Prova" in z.read("dati/azienda.csv").decode()
    assert eliminati == ["/studio/europrojecta-backup/2026-09-01.zip.cifrato"]
    assert "Eliminati 1 backup" in messaggio


def test_esegui_senza_configurazione(monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_passphrase", None)
    with pytest.raises(b.ErroreBackup, match="non configurato"):
        b.esegui()
