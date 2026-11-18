"""Cifratura dei segreti salvati nel database (chiavi API, segreti TOTP).

Fernet (AES-128-CBC + HMAC-SHA256) con una chiave derivata da SECRET_KEY tramite HKDF.
Ogni uso ha il suo "scopo", quindi chiavi diverse: un segreto cifrato per uno scopo non si
decifra con un altro.

Attenzione: se SECRET_KEY cambia, i segreti già salvati non si decifrano più e vanno
reinseriti (chiavi API) o riconfigurati (2FA). Vedi docs/decisioni.md.
"""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


class ErroreCifratura(Exception):
    """Il segreto non si decifra (SECRET_KEY cambiata o dato corrotto)."""


def _chiave(scopo: str) -> bytes:
    segreto = get_settings().secret_key.get_secret_value().encode("utf-8")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"europrojecta:{scopo}".encode(),
    )
    return hkdf.derive(segreto)


def _fernet(scopo: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_chiave(scopo)))


def cifra(testo: str, scopo: str) -> str:
    return _fernet(scopo).encrypt(testo.encode("utf-8")).decode("ascii")


def decifra(token: str, scopo: str) -> str:
    try:
        return _fernet(scopo).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise ErroreCifratura(f"Impossibile decifrare il segreto ({scopo})") from e


def impronta(testo: str, scopo: str) -> str:
    """HMAC-SHA256 con chiave derivata: per i valori da confrontare ma mai rileggere
    (token di sessione, codici di recupero)."""
    return hmac.new(_chiave(scopo), testo.encode("utf-8"), hashlib.sha256).hexdigest()


def anteprima(segreto: str) -> str:
    """Solo le ultime 4 cifre, per mostrare all'utente quale chiave è salvata."""
    return "…" + segreto[-4:] if len(segreto) >= 8 else "…"
