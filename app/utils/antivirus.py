"""Controllo antivirus dei file caricati, con ClamAV (servizio `clamav` in produzione).

Protocollo clamd INSTREAM su TCP: comando `zINSTREAM\\0`, poi blocchi preceduti dalla
lunghezza (4 byte big-endian), chiusi da un blocco di lunghezza 0. Risposta:
`stream: OK` oppure `stream: <nome> FOUND`.

Se CLAMAV_HOST non è impostato (sviluppo) il controllo è saltato. Se è impostato e ClamAV
non risponde, il caricamento è rifiutato: meglio riprovare che accettare un file non controllato.
"""

import socket
import struct
from collections.abc import Iterable
from pathlib import Path

from app.config import get_settings

BLOCCO = 64 * 1024


class FileInfetto(Exception):
    """Il file contiene un virus: non va salvato."""


class AntivirusNonDisponibile(Exception):
    """ClamAV configurato ma non raggiungibile."""


def attivo() -> bool:
    return bool(get_settings().clamav_host)


def _blocchi_da_bytes(dati: bytes) -> Iterable[bytes]:
    for i in range(0, len(dati), BLOCCO):
        yield dati[i : i + BLOCCO]


def _blocchi_da_file(percorso: Path) -> Iterable[bytes]:
    with percorso.open("rb") as f:
        while blocco := f.read(BLOCCO):
            yield blocco


def _scansiona(blocchi: Iterable[bytes]) -> None:
    s = get_settings()
    try:
        with socket.create_connection((s.clamav_host, s.clamav_porta), timeout=60) as conn:
            conn.sendall(b"zINSTREAM\0")
            for blocco in blocchi:
                conn.sendall(struct.pack("!L", len(blocco)) + blocco)
            conn.sendall(struct.pack("!L", 0))
            risposta = b""
            while not risposta.endswith(b"\0"):
                parte = conn.recv(4096)
                if not parte:
                    break
                risposta += parte
    except OSError as e:
        raise AntivirusNonDisponibile(
            "L'antivirus non risponde: il file non è stato salvato. Riprova tra qualche minuto."
        ) from e
    testo = risposta.rstrip(b"\0").decode("utf-8", "replace").strip()
    if testo.endswith("FOUND"):
        nome = testo.removeprefix("stream:").removesuffix("FOUND").strip()
        raise FileInfetto(f"Il file contiene un virus ({nome}) e non è stato salvato.")
    if not testo.endswith("OK"):
        raise AntivirusNonDisponibile(f"Risposta inattesa dell'antivirus: {testo[:100]}")


def controlla_bytes(dati: bytes) -> None:
    if attivo():
        _scansiona(_blocchi_da_bytes(dati))


def controlla_file(percorso: Path) -> None:
    if attivo():
        _scansiona(_blocchi_da_file(percorso))
