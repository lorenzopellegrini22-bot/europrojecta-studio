"""Download HTTP per le fonti, con regole di cortesia e protezione dai file troncati.

- User agent identificabile.
- Al massimo una richiesta al secondo per dominio.
- Download in streaming su file temporaneo. Se il server chiude la connessione prima della
  fine (con Incentivi Italia succede spesso), si riprende con l'header Range finché i byte
  non corrispondono a Content-Length.
"""

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

USER_AGENT = "EuroprojectaStudio/0.1 (raccolta bandi da open data pubblici)"
INTERVALLO_MINIMO_SECONDI = 1.0
MAX_TENTATIVI = 15
TIMEOUT = httpx.Timeout(60.0, connect=20.0)

_ultima_richiesta: dict[str, float] = {}
_lock = threading.Lock()


class ErroreDownload(Exception):
    pass


@dataclass
class Intestazioni:
    last_modified: str | None
    etag: str | None
    content_length: int | None


def _attendi_turno(url: str) -> None:
    """Rispetta l'intervallo minimo tra due richieste allo stesso dominio."""
    dominio = urlsplit(url).netloc
    with _lock:
        trascorso = time.monotonic() - _ultima_richiesta.get(dominio, 0.0)
        if trascorso < INTERVALLO_MINIMO_SECONDI:
            time.sleep(INTERVALLO_MINIMO_SECONDI - trascorso)
        _ultima_richiesta[dominio] = time.monotonic()


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)


def intestazioni(http: httpx.Client, url: str) -> Intestazioni:
    _attendi_turno(url)
    r = http.head(url)
    r.raise_for_status()
    lunghezza = r.headers.get("content-length")
    return Intestazioni(
        last_modified=r.headers.get("last-modified"),
        etag=r.headers.get("etag"),
        content_length=int(lunghezza) if lunghezza and lunghezza.isdigit() else None,
    )


def scarica_completo(http: httpx.Client, url: str, destinazione: Path | None = None) -> Path:
    """Scarica `url` per intero su file e restituisce il percorso.

    Solleva ErroreDownload se dopo MAX_TENTATIVI il file non è completo.
    """
    if destinazione is None:
        fd, nome = tempfile.mkstemp(prefix="fonte-", suffix=".dat")
        destinazione = Path(nome)
        os.close(fd)
    destinazione.write_bytes(b"")
    attesi: int | None = None
    for _ in range(MAX_TENTATIVI):
        scaricati = destinazione.stat().st_size
        headers = {"Range": f"bytes={scaricati}-"} if scaricati else {}
        _attendi_turno(url)
        try:
            with http.stream("GET", url, headers=headers) as r:
                if r.status_code == 416 and attesi is not None and scaricati >= attesi:
                    break
                r.raise_for_status()
                if scaricati and r.status_code != 206:
                    # Il server ignora Range: si ricomincia da capo
                    destinazione.write_bytes(b"")
                    scaricati = 0
                if attesi is None:
                    totale = r.headers.get("content-range", "").rpartition("/")[2]
                    lunghezza = r.headers.get("content-length")
                    if totale.isdigit():
                        attesi = int(totale)
                    elif lunghezza and lunghezza.isdigit():
                        attesi = int(lunghezza) + scaricati
                with destinazione.open("ab") as f:
                    for blocco in r.iter_bytes(chunk_size=256 * 1024):
                        f.write(blocco)
        except (httpx.TransportError, httpx.RemoteProtocolError):
            pass  # connessione interrotta: si riprende dal punto raggiunto
        dimensione = destinazione.stat().st_size
        if attesi is None or dimensione >= attesi:
            return destinazione
    raise ErroreDownload(
        f"Download incompleto dopo {MAX_TENTATIVI} tentativi: "
        f"{destinazione.stat().st_size} byte su {attesi}"
    )


TENTATIVI_PAGINA = 3


def pagina(http: httpx.Client, url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """GET di una pagina o di una risposta API, rispettando l'intervallo per dominio.

    Riprova fino a TENTATIVI_PAGINA volte su errori di rete e risposte 5xx; un 4xx è definitivo.
    """
    ultimo: Exception | None = None
    for _ in range(TENTATIVI_PAGINA):
        _attendi_turno(url)
        try:
            r = http.get(url, params=params)
        except httpx.TransportError as e:
            ultimo = e
            continue
        if r.status_code >= 500:
            ultimo = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            continue
        r.raise_for_status()
        if r.url.host != httpx.URL(url).host:
            # es. reindirizzamento a una verifica anti-bot: non si aggira, la fonte si ferma
            raise ErroreDownload(f"{url}: reindirizzato a {r.url.host}, raccolta non consentita")
        return r
    raise ErroreDownload(f"{url}: {ultimo}")
