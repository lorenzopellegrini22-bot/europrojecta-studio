"""Antivirus: client clamd INSTREAM contro un server finto (nessun ClamAV necessario)."""

import socket
import struct
import threading

import pytest

from app.config import get_settings
from app.utils import antivirus


def _server_finto(risposta_per):
    """Avvia un clamd finto su una porta libera; risponde FOUND se il file contiene EICAR."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    ricevuti = []

    def servi():
        conn, _ = srv.accept()
        with conn:
            comando = b""
            while not comando.endswith(b"\0"):
                comando += conn.recv(1)
            dati = b""
            while True:
                (n,) = struct.unpack("!L", conn.recv(4, socket.MSG_WAITALL))
                if n == 0:
                    break
                dati += conn.recv(n, socket.MSG_WAITALL)
            ricevuti.append((comando, dati))
            conn.sendall(risposta_per(dati))
        srv.close()

    threading.Thread(target=servi, daemon=True).start()
    return srv.getsockname()[1], ricevuti


@pytest.fixture
def clamav(monkeypatch):
    def configura(porta):
        monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
        monkeypatch.setenv("CLAMAV_PORTA", str(porta))
        get_settings.cache_clear()

    yield configura
    monkeypatch.undo()
    get_settings.cache_clear()


def _risposta(dati):
    return b"stream: Eicar-Signature FOUND\0" if b"EICAR" in dati else b"stream: OK\0"


def test_senza_clamav_nessun_controllo():
    assert not antivirus.attivo()
    antivirus.controlla_bytes(b"EICAR qualsiasi")  # nessuna eccezione


def test_file_pulito(clamav):
    porta, ricevuti = _server_finto(_risposta)
    clamav(porta)
    antivirus.controlla_bytes(b"%PDF-1.7 contenuto" * 10000)
    assert ricevuti[0][0] == b"zINSTREAM\0"
    assert ricevuti[0][1] == b"%PDF-1.7 contenuto" * 10000


def test_file_infetto(clamav, tmp_path):
    porta, _ = _server_finto(_risposta)
    clamav(porta)
    f = tmp_path / "virus.pdf"
    f.write_bytes(b"X5O!P%@AP EICAR test")
    with pytest.raises(antivirus.FileInfetto, match="Eicar-Signature"):
        antivirus.controlla_file(f)


def test_clamav_non_raggiungibile(clamav):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    clamav(porta)
    with pytest.raises(antivirus.AntivirusNonDisponibile):
        antivirus.controlla_bytes(b"dati")
