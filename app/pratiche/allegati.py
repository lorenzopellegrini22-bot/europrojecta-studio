"""Archiviazione degli allegati delle pratiche.

- Il file va su disco in `CARTELLA_ALLEGATI/AAAA/MM/<nome casuale>.<estensione>`: il nome
  originale resta solo nel database, così nessun nome scelto dall'utente tocca il filesystem.
  Con ARCHIVIO_ALLEGATI=database (Vercel) il contenuto va nella tabella allegato_contenuto
  e il percorso inizia con "db/".
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
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Allegato, AllegatoContenuto
from app.utils import antivirus

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
    try:
        antivirus.controlla_file(destinazione)
    except (antivirus.FileInfetto, antivirus.AntivirusNonDisponibile) as e:
        destinazione.unlink(missing_ok=True)
        raise ErroreAllegato(str(e)) from e
    return relativo.as_posix(), TIPI_AMMESSI[ext], dimensione, impronta.hexdigest()


def percorso_assoluto(relativo: str) -> Path:
    """Percorso sul disco, controllando che resti dentro la cartella degli allegati."""
    base = cartella()
    p = (base / relativo).resolve()
    if base not in p.parents:
        raise ErroreAllegato("Percorso non valido.")
    return p


def elimina_file(relativo: str) -> None:
    if relativo.startswith(PREFISSO_DB):
        return  # il contenuto nel database si cancella con la riga dell'allegato
    with contextlib.suppress(ErroreAllegato):
        percorso_assoluto(relativo).unlink(missing_ok=True)


# --- Archivio nel database (ARCHIVIO_ALLEGATI=database) ----------------------------------

PREFISSO_DB = "db/"


def nel_database() -> bool:
    return get_settings().archivio_allegati == "database"


def _leggi(sorgente: BinaryIO, nome_originale: str) -> tuple[bytes, str]:
    """Stessi controlli di salva(), ma il file resta in memoria: (dati, estensione)."""
    ext = estensione(nome_originale)
    if ext not in TIPI_AMMESSI:
        ammessi = ", ".join(sorted(e.lstrip(".") for e in TIPI_AMMESSI))
        raise ErroreAllegato(f"Tipo di file non ammesso. Ammessi: {ammessi}.")
    limite = get_settings().allegato_max_mb * 1024 * 1024
    dati = sorgente.read(limite + 1)
    if len(dati) > limite:
        mb = get_settings().allegato_max_mb
        raise ErroreAllegato(f"File troppo grande: il limite è {mb} MB.")
    if not dati:
        raise ErroreAllegato("Il file è vuoto.")
    try:
        antivirus.controlla_bytes(dati)
    except (antivirus.FileInfetto, antivirus.AntivirusNonDisponibile) as e:
        raise ErroreAllegato(str(e)) from e
    return dati, ext


def nuovo_allegato(
    db: Session,
    sorgente: BinaryIO,
    nome_originale: str,
    *,
    azienda_id: int,
    pratica_id: int | None = None,
    pratica_documento_id: int | None = None,
    caricato_da: int | None = None,
) -> Allegato:
    """Salva il file (su disco o nel database, secondo ARCHIVIO_ALLEGATI) e aggiunge
    l'allegato alla sessione. Solleva ErroreAllegato se il file non va bene."""
    campi = {
        "azienda_id": azienda_id,
        "pratica_id": pratica_id,
        "pratica_documento_id": pratica_documento_id,
        "nome_originale": nome_originale,
        "caricato_da": caricato_da,
    }
    if nel_database():
        dati, ext = _leggi(sorgente, nome_originale)
        oggi = date.today()
        allegato = Allegato(
            **campi,
            percorso=f"{PREFISSO_DB}{oggi:%Y}/{oggi:%m}/{secrets.token_hex(16)}{ext}",
            mime=TIPI_AMMESSI[ext],
            dimensione=len(dati),
            sha256=hashlib.sha256(dati).hexdigest(),
            contenuto=AllegatoContenuto(dati=dati),
        )
    else:
        relativo, mime, dimensione, sha = salva(sorgente, nome_originale)
        allegato = Allegato(
            **campi, percorso=relativo, mime=mime, dimensione=dimensione, sha256=sha
        )
    db.add(allegato)
    return allegato


def dati_allegato(allegato: Allegato) -> bytes | None:
    """Contenuto del file (dal database o dal disco); None se non si trova più."""
    if allegato.percorso.startswith(PREFISSO_DB):
        return allegato.contenuto.dati if allegato.contenuto else None
    try:
        percorso = percorso_assoluto(allegato.percorso)
    except ErroreAllegato:
        return None
    return percorso.read_bytes() if percorso.is_file() else None


def risposta_download(allegato: Allegato) -> Response:
    """Il file da scaricare: dal disco in streaming, dal database in un colpo solo."""
    if not allegato.percorso.startswith(PREFISSO_DB):
        try:
            percorso = percorso_assoluto(allegato.percorso)
        except ErroreAllegato as e:
            raise HTTPException(404) from e
        if not percorso.is_file():
            raise HTTPException(404, "File non trovato sul server")
        return FileResponse(
            percorso,
            media_type=allegato.mime,
            filename=allegato.nome_originale,
            content_disposition_type="attachment",
        )
    if allegato.contenuto is None:
        raise HTTPException(404, "File non trovato nel database")
    nome = allegato.nome_originale
    ascii_nome = nome.encode("ascii", "ignore").decode() or "documento"
    ascii_nome = ascii_nome.replace('"', "")
    disposizione = f"attachment; filename=\"{ascii_nome}\"; filename*=UTF-8''{quote(nome)}"
    return Response(
        allegato.contenuto.dati,
        media_type=allegato.mime,
        headers={"Content-Disposition": disposizione},
    )
