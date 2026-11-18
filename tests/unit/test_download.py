"""Downloader: deve ricomporre un file troncato dal server usando l'header Range."""

import httpx
import pytest

from app.sources import http

CONTENUTO = bytes(range(256)) * 400  # ~100 KB


@pytest.fixture(autouse=True)
def senza_attese(monkeypatch):
    monkeypatch.setattr(http, "INTERVALLO_MINIMO_SECONDI", 0)


def _server_che_tronca(pezzo: int):
    """Simula Incentivi Italia: ogni risposta si interrompe dopo `pezzo` byte."""
    richieste: list[str | None] = []

    def gestore(request: httpx.Request) -> httpx.Response:
        intervallo = request.headers.get("range")
        richieste.append(intervallo)
        inizio = int(intervallo.split("=")[1].rstrip("-")) if intervallo else 0
        corpo = CONTENUTO[inizio : inizio + pezzo]
        if intervallo:
            return httpx.Response(
                206,
                content=corpo,
                headers={"content-range": f"bytes {inizio}-{len(CONTENUTO) - 1}/{len(CONTENUTO)}"},
            )
        return httpx.Response(200, content=corpo, headers={"x-lunghezza": str(len(CONTENUTO))})

    return gestore, richieste


def test_ricompone_file_troncato(tmp_path, monkeypatch):
    gestore, richieste = _server_che_tronca(30_000)
    client = httpx.Client(transport=httpx.MockTransport(gestore))
    # La prima risposta dichiara la lunghezza completa ma ne invia solo una parte
    originale = client.stream

    def stream_con_lunghezza(metodo, url, headers=None):
        ctx = originale(metodo, url, headers=headers)
        return _ForzaLunghezza(ctx)

    monkeypatch.setattr(client, "stream", stream_con_lunghezza)
    destinazione = tmp_path / "file.json"
    http.scarica_completo(client, "https://esempio.test/file.json", destinazione)
    assert destinazione.read_bytes() == CONTENUTO
    assert richieste[0] is None and richieste[1] == "bytes=30000-"
    assert len(richieste) == 4


def test_errore_se_non_si_completa(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "MAX_TENTATIVI", 2)
    gestore, _ = _server_che_tronca(10)
    client = httpx.Client(transport=httpx.MockTransport(gestore))
    originale = client.stream
    monkeypatch.setattr(
        client,
        "stream",
        lambda m, u, headers=None: _ForzaLunghezza(originale(m, u, headers=headers)),
    )
    with pytest.raises(http.ErroreDownload):
        http.scarica_completo(client, "https://esempio.test/file.json", tmp_path / "f")


class _ForzaLunghezza:
    """Aggiunge content-length = file completo alla prima risposta (come fa il server reale)."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __enter__(self):
        r = self._ctx.__enter__()
        if r.status_code == 200:
            r.headers["content-length"] = r.headers["x-lunghezza"]
        return r

    def __exit__(self, *args):
        return self._ctx.__exit__(*args)
