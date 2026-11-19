"""Invito via email per i nuovi utenti: un link personale, valido 72 ore e usabile una volta,
con cui la persona sceglie la propria password. Nessuna password viaggia per email.

Il token esiste solo nel link: nel database c'è la sua impronta (HMAC, come per le sessioni).
Dopo aver scelto la password si entra dal login e si configura la 2FA (obbligatoria).
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.password import hash_password, valida_nuova_password
from app.config import get_settings
from app.db import get_db
from app.models import Utente
from app.sicurezza.cifratura import impronta
from app.web import render

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]

SCOPO = "invito_utente"
DURATA = timedelta(hours=72)


def crea_invito(utente: Utente) -> str:
    """Nuovo token (sostituisce l'eventuale invito precedente). Restituisce il link."""
    token = secrets.token_urlsafe(32)
    utente.invito_hash = impronta(token, SCOPO)
    utente.invito_scade_il = datetime.now(UTC) + DURATA
    return f"{get_settings().url_pubblico.rstrip('/')}/invito/{token}"


def testo_email(utente: Utente, link: str, titolare: Utente) -> tuple[str, str]:
    return (
        "Il tuo accesso a Europrojecta Studio",
        "\n".join(
            [
                f"Buongiorno {utente.nome},",
                "",
                f"{titolare.nome} ti ha creato un accesso a Europrojecta Studio.",
                "Apri questo link per scegliere la tua password (vale 72 ore e una volta sola):",
                link,
                "",
                "Al primo accesso configurerai anche il codice sul telefono (app di "
                "autenticazione come Google Authenticator o Microsoft Authenticator).",
                "",
                "Se non aspettavi questa email, ignorala.",
            ]
        ),
    )


def _utente(db: Session, token: str) -> Utente | None:
    if not token or len(token) > 100:
        return None
    u = db.scalar(select(Utente).where(Utente.invito_hash == impronta(token, SCOPO)))
    if u is None or not u.attivo or u.invito_scade_il is None:
        return None
    if u.invito_scade_il < datetime.now(UTC):
        return None
    return u


@router.get("/invito/{token}")
def pagina(request: Request, token: str, db: Db) -> Response:
    u = _utente(db, token)
    return render(request, "auth/invito.html", {"valido": u is not None, "u": u}, 200 if u else 404)


@router.post("/invito/{token}")
def scegli_password(
    request: Request,
    token: str,
    db: Db,
    nuova: Annotated[str, Form()],
    conferma: Annotated[str, Form()],
) -> Response:
    """Pagina pubblica: la protezione è il token segreto e monouso (e il controllo
    dell'origine delle richieste in app/main.py)."""
    u = _utente(db, token)
    if u is None:
        return render(request, "auth/invito.html", {"valido": False}, 404)
    errore = valida_nuova_password(nuova)
    if not errore and nuova != conferma:
        errore = "Le due password non coincidono."
    if errore:
        return render(request, "auth/invito.html", {"valido": True, "u": u, "errore": errore}, 400)
    u.password_hash = hash_password(nuova)
    u.invito_hash = None
    u.invito_scade_il = None
    u.deve_cambiare_password = False
    audit.registra(db, None, "utente", u.id, "modifica", {"password": "scelta dall'invito"})
    db.commit()
    return RedirectResponse("/login?invito=1", status_code=303)
