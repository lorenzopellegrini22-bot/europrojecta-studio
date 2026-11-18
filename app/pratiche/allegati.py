"""Archiviazione degli allegati delle pratiche.

- Il file va su disco in `CARTELLA_ALLEGATI/AAAA/MM/<nome casuale>.<estensione>`: il nome
  originale resta solo nel database, così nessun nome scelto dall'utente tocca il filesystem.
- Tipi ammessi: documenti d'ufficio, PDF (anche firmati .p7m), immagini, archivi zip, email.
- Limite di dimensione da ALLEGATO_MAX_MB; impronta SHA-256 per riconoscere i duplicati.
- Il download passa sempre dall'app (login obbligatorio), mai da un link statico.
"""

import contextlib
import hashlib
import secrets
from datetime import date
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

TIPI_AMMESSI = {
    ".pdf": "application/pdf",
    ".p7m": "application/pkcs7-mime",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".zip": "application/zip",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}
BLOCCO = 1024 * 1024


class ErroreAllegato(Exception):
    pass


def cartella() -> Path:
    return Path(get_settings().cartella_allegati).resolve()


def estensione(nome: str) -> str:
    nome = nome.lower().strip()
    # .pdf.p7m: conta l'ultima estensione
    return Path(nome).suffix


def salva(sorgente: BinaryIO, nome_originale: str) -> tuple[str, str, int, str]:
    """Salva il file e restituisce (percorso relativo, mime, dimensione, sha256)."""
    ext = estensione(nome_originale)
    if ext not in TIPI_AMMESSI:
        ammessi = ", ".join(sorted(e.lstrip(".") for e in TIPI_AMMESSI))
        raise ErroreAllegato(f"Tipo di file non ammesso. Ammessi: {ammessi}.")
    limite = get_settings().allegato_max_mb * 1024 * 1024
    oggi = date.today()
    relativo = Path(f"{oggi:%Y}") / f"{oggi:%m}" / f"{secrets.token_hex(16)}{ext}"
    destinazione = cartella() / relativo
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    impronta = hashlib.sha256()
    dimensione = 0
    try:
        with destinazione.open("xb") as f:
            while blocco := sorgente.read(BLOCCO):
                dimensione += len(blocco)
                if dimensione > limite:
                    raise ErroreAllegato(
                        f"File troppo grande: il limite è {get_settings().allegato_max_mb} MB."
                    )
                impronta.update(blocco)
                f.write(blocco)
    except BaseException:
        destinazione.unlink(missing_ok=True)
        raise
    if dimensione == 0:
        destinazione.unlink(missing_ok=True)
        raise ErroreAllegato("Il file è vuoto.")
    return relativo.as_posix(), TIPI_AMMESSI[ext], dimensione, impronta.hexdigest()


def percorso_assoluto(relativo: str) -> Path:
    """Percorso sul disco, controllando che resti dentro la cartella degli allegati."""
    base = cartella()
    p = (base / relativo).resolve()
    if base not in p.parents:
        raise ErroreAllegato("Percorso non valido.")
    return p


def elimina_file(relativo: str) -> None:
    with contextlib.suppress(ErroreAllegato):
        percorso_assoluto(relativo).unlink(missing_ok=True)
