"""2FA con codici TOTP (app tipo Google Authenticator, Microsoft Authenticator, 1Password…)
e codici di recupero monouso."""

import hmac
import secrets
from datetime import UTC, datetime, timedelta

import pyotp
import segno

from app import NOME_APP
from app.sicurezza.cifratura import impronta

SCOPO_RECUPERO = "codice_recupero"
NUMERO_CODICI_RECUPERO = 10
_ALFABETO_RECUPERO = "abcdefghjkmnpqrstuvwxyz23456789"  # senza caratteri ambigui (0/o, 1/l/i)


def nuovo_segreto() -> str:
    return pyotp.random_base32()


def uri_provisioning(segreto: str, email: str) -> str:
    return pyotp.TOTP(segreto).provisioning_uri(name=email, issuer_name=NOME_APP)


def qr_svg(uri: str) -> str:
    """QR code come SVG inline (nessuna risorsa esterna, compatibile con la CSP)."""
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#111", light="#fff")


def verifica_codice(
    segreto: str, codice: str, ultimo_intervallo: int | None, adesso: datetime | None = None
) -> int | None:
    """Verifica un codice a 6 cifre con tolleranza di ±1 intervallo (30 s).

    Restituisce l'intervallo usato (da salvare per impedire il riuso), o None se il codice
    non è valido o è già stato usato.
    """
    codice = "".join(c for c in codice if c.isdigit())
    if len(codice) != 6:
        return None
    totp = pyotp.TOTP(segreto)
    adesso = adesso or datetime.now(UTC)
    for scarto in (0, -1, 1):
        istante = adesso + timedelta(seconds=30 * scarto)
        if hmac.compare_digest(totp.at(istante), codice):
            intervallo = totp.timecode(istante)
            if ultimo_intervallo is not None and intervallo <= ultimo_intervallo:
                return None
            return intervallo
    return None


def genera_codici_recupero() -> list[str]:
    """10 codici tipo 'abcde-fghjk' (50 bit di entropia ciascuno)."""
    codici = []
    for _ in range(NUMERO_CODICI_RECUPERO):
        grezzo = "".join(secrets.choice(_ALFABETO_RECUPERO) for _ in range(10))
        codici.append(f"{grezzo[:5]}-{grezzo[5:]}")
    return codici


def normalizza_codice_recupero(codice: str) -> str:
    pulito = "".join(c for c in codice.lower() if c.isalnum())
    return f"{pulito[:5]}-{pulito[5:]}" if len(pulito) == 10 else pulito


def hash_codice_recupero(codice: str) -> str:
    return impronta(normalizza_codice_recupero(codice), SCOPO_RECUPERO)
