"""Cifratura dei segreti salvati nel database (chiavi API, segreti TOTP).

Fernet (AES-128-CBC + HMAC-SHA256) con una chiave derivata da SECRET_KEY tramite HKDF.
Ogni uso ha il suo "scopo", quindi chiavi diverse: un segreto cifrato per uno scopo non si
decifra con un altro.

Rotazione della chiave (MultiFernet): si mette la nuova in SECRET_KEY e la vecchia in
SECRET_KEY_PRECEDENTI; i segreti si leggono con entrambe e `python -m app.cli ruota-chiavi`
li ricifra con la nuova. Dopo si può togliere la vecchia. Le impronte (sessioni, codici di
recupero) non si possono ricalcolare: le sessioni vengono chiuse e i codici di recupero
vanno rigenerati da «Il mio account». Vedi docs/messa-in-produzione.md.
"""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


class ErroreCifratura(Exception):
    """Il segreto non si decifra (SECRET_KEY cambiata o dato corrotto)."""


def _segreti() -> list[bytes]:
    s = get_settings()
    vecchie = s.secret_key_precedenti.get_secret_value() if s.secret_key_precedenti else ""
    return [s.secret_key.get_secret_value().encode("utf-8")] + [
        v.strip().encode("utf-8") for v in vecchie.split(",") if v.strip()
    ]


def _chiave(scopo: str, segreto: bytes | None = None) -> bytes:
    segreto = segreto or _segreti()[0]
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"europrojecta:{scopo}".encode(),
    )
    return hkdf.derive(segreto)


def _fernet(scopo: str) -> MultiFernet:
    """Cifra con la chiave attuale, decifra con l'attuale o con le precedenti."""
    return MultiFernet([Fernet(base64.urlsafe_b64encode(_chiave(scopo, s))) for s in _segreti()])


def cifra(testo: str, scopo: str) -> str:
    return _fernet(scopo).encrypt(testo.encode("utf-8")).decode("ascii")


def decifra(token: str, scopo: str) -> str:
    try:
        return _fernet(scopo).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise ErroreCifratura(f"Impossibile decifrare il segreto ({scopo})") from e


def ricifra(token: str, scopo: str) -> str:
    """Lo stesso segreto cifrato con la chiave attuale (rotazione)."""
    try:
        return _fernet(scopo).rotate(token.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError) as e:
        raise ErroreCifratura(f"Impossibile decifrare il segreto ({scopo})") from e


def impronta(testo: str, scopo: str) -> str:
    """HMAC-SHA256 con chiave derivata: per i valori da confrontare ma mai rileggere
    (token di sessione, codici di recupero)."""
    return hmac.new(_chiave(scopo), testo.encode("utf-8"), hashlib.sha256).hexdigest()


def anteprima(segreto: str) -> str:
    """Solo le ultime 4 cifre, per mostrare all'utente quale chiave è salvata."""
    return "…" + segreto[-4:] if len(segreto) >= 8 else "…"
