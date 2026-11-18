"""Password con argon2id (parametri di default di argon2-cffi, conformi a RFC 9106)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

LUNGHEZZA_MINIMA = 12

# Hash di una password a caso: si verifica anche quando l'utente non esiste,
# così il tempo di risposta non rivela quali email sono registrate.
_HASH_FITTIZIO = _hasher.hash("password-fittizia-per-tempi-costanti")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verifica_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _HASH_FITTIZIO, password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def serve_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def valida_nuova_password(password: str) -> str | None:
    """Restituisce un messaggio d'errore, o None se la password va bene."""
    if len(password) < LUNGHEZZA_MINIMA:
        return f"La password deve avere almeno {LUNGHEZZA_MINIMA} caratteri."
    return None
