"""Download HTTP per le fonti, con regole di cortesia e protezione dai file troncati.

- User agent identificabile.
- Al massimo una richiesta al secondo per dominio.
- Download in streaming su file temporaneo. Se il server chiude la connessione prima della
  fine (con Incentivi Italia succede spesso), si riprende con l'header Range finché i byte
  non corrispondono a Content-Length.
"""

import ipaddress
import os
import re
import socket
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


def indirizzo_pubblico(url: str) -> None:
    """Protezione SSRF: gli URL arrivano anche da dati esterni (TED, pagine delle
    piattaforme), quindi il server contatta solo indirizzi pubblici in http/https sulle porte
    standard. Vale per ogni richiesta, compresi i passaggi dei reindirizzamenti."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as e:
        raise ErroreDownload(f"Indirizzo non valido: {url[:120]}") from e
    if u.scheme not in ("http", "https") or not u.host:
        raise ErroreDownload(f"Indirizzo non consentito: {url[:120]}")
    if u.port not in (None, 80, 443):
        raise ErroreDownload(f"Porta non consentita: {url[:120]}")
    try:
        risolti = {i[4][0] for i in socket.getaddrinfo(u.host, None)}
    except (socket.gaierror, UnicodeError):
        return  # il nome non esiste: la richiesta fallirà da sola
    for ip in risolti:
        indirizzo = ipaddress.ip_address(ip.split("%")[0])
        if not indirizzo.is_global or indirizzo.is_multicast:
            raise ErroreDownload(f"Indirizzo interno non consentito: {u.host}")


def _controlla_richiesta(request: httpx.Request) -> None:
    indirizzo_pubblico(str(request.url))


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        event_hooks={"request": [_controlla_richiesta]},
    )


MAX_PAGINA_HTML = 5 * 1024 * 1024


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)


def decodifica(dati: bytes, charset: str | None = None) -> str:
    """Testo della pagina: codifica dell'intestazione HTTP, poi quella del <meta>, poi UTF-8,
    altrimenti Windows-1252 (i siti che non la dichiarano: niente «�» al posto di «–», «à»)."""
    if not charset and (m := _META_CHARSET.search(dati[:4096])):
        charset = m.group(1).decode("ascii", "ignore")
    for codifica in (charset, "utf-8"):
        if not codifica:
            continue
        try:
            return dati.decode(codifica)
        except (UnicodeDecodeError, LookupError):
            continue
    return dati.decode("cp1252", "replace")


def pagina_html(http: httpx.Client, url: str) -> tuple[str, str] | None:
    """Pagina HTML (testo, url finale) letta in streaming con un limite di dimensione; None
    se la risposta non è HTML (un file: non si scarica)."""
    _attendi_turno(url)
    with http.stream("GET", url) as r:
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", ""):
            return None
        dati = b""
        for blocco in r.iter_bytes(chunk_size=64 * 1024):
            dati += blocco
            if len(dati) > MAX_PAGINA_HTML:
                raise ErroreDownload(f"Pagina troppo grande: {url[:120]}")
        return decodifica(dati, r.charset_encoding), str(r.url)


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
