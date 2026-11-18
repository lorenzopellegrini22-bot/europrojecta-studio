"""Backup giornaliero cifrato su uno storage esterno compatibile S3, per le installazioni
senza restic (Vercel): sul server con docker-compose i backup li fa il servizio `backup`.

Ogni notte (cron /cron/backup):
1. crea l'esportazione completa (tutte le tabelle in CSV e tutti i file caricati, come
   «Esporta tutti i dati»);
2. la cifra con BACKUP_PASSPHRASE (scrypt + AES-256-GCM a blocchi, ogni blocco autenticato
   e numerato: un file troncato o modificato non si apre);
3. la carica su BACKUP_S3_BUCKET (Cloudflare R2, Backblaze B2, AWS S3, Scaleway...) con
   firma AWS Signature V4, senza librerie esterne;
4. cancella i backup più vecchi di BACKUP_GIORNI giorni.

Per aprire un backup: `python -m app.cli backup-decifra --file <file> --uscita export.zip`.
Senza BACKUP_PASSPHRASE la copia non si apre: va conservata anche fuori dall'app.
"""

import hashlib
import hmac
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.config import get_settings

MAGIA = b"EPBK1"
BLOCCO = 4 * 1024 * 1024
HASH_VUOTO = hashlib.sha256(b"").hexdigest()
NOME = re.compile(r"(\d{4}-\d{2}-\d{2})\.zip\.cifrato$")


class ErroreBackup(Exception):
    pass


# --- Cifratura ----------------------------------------------------------------------------


def _chiave(passphrase: str, sale: bytes) -> bytes:
    return Scrypt(salt=sale, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _aad(indice: int, ultimo: bool) -> bytes:
    return MAGIA + indice.to_bytes(4, "big") + (b"\x01" if ultimo else b"\x00")


def cifra_file(sorgente: Path, destinazione: Path, passphrase: str) -> str:
    """Cifra `sorgente` in `destinazione`. Restituisce lo SHA-256 del file cifrato."""
    sale, prefisso = os.urandom(16), os.urandom(8)
    aes = AESGCM(_chiave(passphrase, sale))
    impronta = hashlib.sha256()
    with sorgente.open("rb") as entrata, destinazione.open("wb") as uscita:

        def scrivi(dati: bytes) -> None:
            impronta.update(dati)
            uscita.write(dati)

        scrivi(MAGIA + sale + prefisso)
        indice = 0
        blocco = entrata.read(BLOCCO)
        while True:
            successivo = entrata.read(BLOCCO)
            ultimo = not successivo
            nonce = prefisso + indice.to_bytes(4, "big")
            cifrato = aes.encrypt(nonce, blocco, _aad(indice, ultimo))
            scrivi(len(cifrato).to_bytes(4, "big") + cifrato)
            if ultimo:
                break
            blocco, indice = successivo, indice + 1
    return impronta.hexdigest()


def decifra_file(sorgente: Path, destinazione: Path, passphrase: str) -> None:
    with sorgente.open("rb") as entrata, destinazione.open("wb") as uscita:
        testa = entrata.read(len(MAGIA) + 24)
        if len(testa) != len(MAGIA) + 24 or not testa.startswith(MAGIA):
            raise ErroreBackup("Non è un backup di Europrojecta Studio.")
        sale, prefisso = testa[len(MAGIA) : len(MAGIA) + 16], testa[len(MAGIA) + 16 :]
        aes = AESGCM(_chiave(passphrase, sale))
        indice, finito = 0, False
        while lunghezza := entrata.read(4):
            if finito:
                raise ErroreBackup("Dati in più dopo la fine del backup.")
            cifrato = entrata.read(int.from_bytes(lunghezza, "big"))
            nonce = prefisso + indice.to_bytes(4, "big")
            try:
                chiaro = aes.decrypt(nonce, cifrato, _aad(indice, False))
            except InvalidTag:
                try:
                    chiaro = aes.decrypt(nonce, cifrato, _aad(indice, True))
                except InvalidTag as e:
                    raise ErroreBackup("Passphrase sbagliata o backup danneggiato.") from e
                finito = True
            uscita.write(chiaro)
            indice += 1
        if not finito:
            raise ErroreBackup("Backup incompleto: manca la parte finale.")


# --- Storage S3 (AWS Signature V4) --------------------------------------------------------


def firma_sigv4(
    metodo: str,
    url: str,
    intestazioni: dict[str, str],
    hash_contenuto: str,
    chiave: str,
    segreto: str,
    regione: str,
    ora: datetime,
) -> dict[str, str]:
    """Intestazioni firmate (Authorization compresa) per una richiesta S3."""
    parti = urlsplit(url)
    data_amz = ora.strftime("%Y%m%dT%H%M%SZ")
    giorno = data_amz[:8]
    h = {k.lower(): " ".join(str(v).split()) for k, v in intestazioni.items()}
    h["host"] = parti.netloc
    h["x-amz-date"] = data_amz
    h["x-amz-content-sha256"] = hash_contenuto
    firmate = ";".join(sorted(h))
    query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted(parse_qsl(parti.query, keep_blank_values=True))
    )
    canonica = "\n".join(
        [
            metodo,
            quote(parti.path or "/", safe="/-_.~%"),
            query,
            "".join(f"{k}:{h[k]}\n" for k in sorted(h)),
            firmate,
            hash_contenuto,
        ]
    )
    ambito = f"{giorno}/{regione}/s3/aws4_request"
    da_firmare = "\n".join(
        ["AWS4-HMAC-SHA256", data_amz, ambito, hashlib.sha256(canonica.encode()).hexdigest()]
    )
    k = ("AWS4" + segreto).encode()
    for parte in (giorno, regione, "s3", "aws4_request"):
        k = hmac.new(k, parte.encode(), hashlib.sha256).digest()
    firma = hmac.new(k, da_firmare.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={chiave}/{ambito}, SignedHeaders={firmate}, Signature={firma}"
    )
    return h


class StorageS3:
    def __init__(self, client: httpx.Client | None = None) -> None:
        s = get_settings()
        if not (s.backup_s3_endpoint and s.backup_s3_bucket and s.backup_s3_chiave):
            raise ErroreBackup("Storage dei backup non configurato.")
        if s.backup_s3_segreto is None:
            raise ErroreBackup("Manca BACKUP_S3_SEGRETO.")
        self.base = f"{s.backup_s3_endpoint.rstrip('/')}/{s.backup_s3_bucket}"
        self.chiave, self.segreto = s.backup_s3_chiave, s.backup_s3_segreto.get_secret_value()
        self.regione = s.backup_s3_regione
        self.client = client or httpx.Client(timeout=120)

    def _richiesta(
        self, metodo: str, url: str, hash_contenuto: str = HASH_VUOTO, **kw
    ) -> httpx.Response:
        intestazioni = firma_sigv4(
            metodo,
            url,
            kw.pop("intestazioni", {}),
            hash_contenuto,
            self.chiave,
            self.segreto,
            self.regione,
            datetime.now(UTC),
        )
        r = self.client.request(metodo, url, headers=intestazioni, **kw)
        if r.status_code >= 300:
            raise ErroreBackup(f"Storage: {metodo} risposta {r.status_code} {r.text[:200]}")
        return r

    def carica(self, nome: str, file: Path, sha256: str) -> None:
        with file.open("rb") as f:
            self._richiesta(
                "PUT",
                f"{self.base}/{quote(nome)}",
                sha256,
                intestazioni={"content-length": str(file.stat().st_size)},
                content=f,
            )

    def elenca(self, prefisso: str) -> list[str]:
        nomi: list[str] = []
        continua: str | None = None
        while True:
            query = f"list-type=2&prefix={quote(prefisso, safe='')}"
            if continua:
                query += f"&continuation-token={quote(continua, safe='')}"
            r = self._richiesta("GET", f"{self.base}?{query}")
            radice = ET.fromstring(r.content)
            ns = radice.tag.split("}")[0] + "}" if radice.tag.startswith("{") else ""
            nomi += [e.text or "" for e in radice.iter(f"{ns}Key")]
            continua = radice.findtext(f"{ns}NextContinuationToken")
            if not continua:
                return nomi

    def elimina(self, nome: str) -> None:
        self._richiesta("DELETE", f"{self.base}/{quote(nome)}")


# --- Job ----------------------------------------------------------------------------------


def esegui(client: httpx.Client | None = None, oggi: date | None = None) -> str:
    s = get_settings()
    if s.backup_passphrase is None or not s.backup_s3_bucket:
        raise ErroreBackup(
            "Backup esterno non configurato: servono BACKUP_PASSPHRASE e lo storage "
            "(BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, BACKUP_S3_CHIAVE, BACKUP_S3_SEGRETO)."
        )
    from app.db import get_sessionmaker
    from app.esportazione import crea_zip

    storage = StorageS3(client)
    oggi = oggi or date.today()
    prefisso = s.backup_s3_prefisso.strip("/") + "/"
    nome = f"{prefisso}{oggi.isoformat()}.zip.cifrato"
    cartella = Path(tempfile.mkdtemp(prefix="backup-"))
    try:
        export, cifrato = cartella / "export.zip", cartella / "export.zip.cifrato"
        with get_sessionmaker()() as db:
            conteggi = crea_zip(db, export)
        sha = cifra_file(export, cifrato, s.backup_passphrase.get_secret_value())
        dimensione = cifrato.stat().st_size
        storage.carica(nome, cifrato, sha)
    finally:
        for f in cartella.iterdir():
            f.unlink(missing_ok=True)
        cartella.rmdir()
    limite = oggi - timedelta(days=s.backup_giorni)
    eliminati = 0
    for vecchio in storage.elenca(prefisso):
        trovato = NOME.search(vecchio)
        if trovato and date.fromisoformat(trovato.group(1)) < limite:
            storage.elimina(vecchio)
            eliminati += 1
    righe = sum(v for k, v in conteggi.items() if k != "file_allegati")
    return (
        f"Backup {nome} caricato ({dimensione / 1_048_576:.1f} MB, {righe} righe, "
        f"{conteggi.get('file_allegati', 0)} file). Eliminati {eliminati} backup più vecchi "
        f"di {s.backup_giorni} giorni."
    )
