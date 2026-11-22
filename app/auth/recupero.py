"""«Password dimenticata»: chi non ricorda la password riceve per email un link personale,
valido un'ora e usabile una volta, con cui sceglie una password nuova.

- La risposta è sempre la stessa, che l'email sia registrata o no: la pagina non rivela chi
  ha un accesso.
- Nel database c'è solo l'impronta del token (come per inviti e sessioni).
- Limiti: un link ogni 2 minuti per persona e al massimo 5 richieste ogni 15 minuti dallo
  stesso indirizzo IP (registrate in log_accesso con esito «recupero_password»).
- Dopo il cambio si chiudono tutte le sessioni aperte e si azzera il blocco per tentativi
  falliti; la 2FA resta: al login si chiede come sempre il codice del telefono.
- L'email parte con il server SMTP di Impostazioni → Integrazioni; se non è configurato,
  il titolare può ancora usare `python -m app.cli reimposta-password`.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth.password import hash_password, valida_nuova_password
from app.auth.sessioni import elimina_sessioni_utente
from app.config import get_settings
from app.db import get_db
from app.models import LogAccesso, Utente
from app.sicurezza.cifratura import impronta
from app.web import render

router = APIRouter(prefix="/password-dimenticata")
Db = Annotated[Session, Depends(get_db)]

SCOPO = "recupero_password"
DURATA = timedelta(hours=1)
ATTESA_TRA_INVII = timedelta(minutes=2)
MAX_RICHIESTE_IP = 5
FINESTRA_IP = timedelta(minutes=15)


def crea_link(utente: Utente) -> str:
    token = secrets.token_urlsafe(32)
    utente.recupero_hash = impronta(token, SCOPO)
    utente.recupero_scade_il = datetime.now(UTC) + DURATA
    return f"{get_settings().url_pubblico.rstrip('/')}/password-dimenticata/{token}"


def testo_email(utente: Utente, link: str) -> tuple[str, str]:
    return (
        "Nuova password per Europrojecta Studio",
        "\n".join(
            [
                f"Buongiorno {utente.nome},",
                "",
                "abbiamo ricevuto una richiesta di nuova password per il tuo accesso.",
                "Apri questo link per sceglierla (vale un'ora e una volta sola):",
                link,
                "",
                "Se non sei stato tu, ignora questa email: la password attuale resta valida.",
            ]
        ),
    )


def _troppe_richieste(db: Session, ip: str | None) -> bool:
    if not ip:
        return False
    da = datetime.now(UTC) - FINESTRA_IP
    n = db.scalar(
        select(func.count())
        .select_from(LogAccesso)
        .where(LogAccesso.ip == ip, LogAccesso.esito == SCOPO, LogAccesso.creato_il >= da)
    )
    return (n or 0) >= MAX_RICHIESTE_IP


def _utente_dal_token(db: Session, token: str) -> Utente | None:
    if not token or len(token) > 100:
        return None
    u = db.scalar(select(Utente).where(Utente.recupero_hash == impronta(token, SCOPO)))
    if u is None or not u.attivo or u.recupero_scade_il is None:
        return None
    if u.recupero_scade_il < datetime.now(UTC):
        return None
    return u


@router.get("")
def pagina(request: Request) -> Response:
    return render(request, "auth/password_dimenticata.html")


@router.post("")
def richiesta(request: Request, db: Db, email: Annotated[str, Form()]) -> Response:
    from app.comunicazioni import invia_registrando

    email = email.strip().lower()[:254]
    ip = request.client.host if request.client else None
    contesto = {"inviata": True, "email": email}
    if _troppe_richieste(db, ip):
        return render(request, "auth/password_dimenticata.html", contesto)
    u = db.scalar(select(Utente).where(Utente.email == email))
    db.add(
        LogAccesso(
            email_tentata=email,
            utente_id=u.id if u else None,
            esito=SCOPO,
            ip=ip,
            user_agent=(request.headers.get("user-agent") or "")[:500],
        )
    )
    db.commit()
    adesso = datetime.now(UTC)
    recente = (
        u is not None
        and u.recupero_scade_il is not None
        and u.recupero_scade_il - DURATA > adesso - ATTESA_TRA_INVII
    )
    if u is not None and u.attivo and not recente:
        link = crea_link(u)
        db.commit()
        oggetto, testo = testo_email(u, link)
        ok, _ = invia_registrando(db, tipo="avviso", destinatario=u.email, oggetto=oggetto,
                                  testo=testo, utente=u)  # fmt: skip
        if not ok:
            u.recupero_hash = u.recupero_scade_il = None
            db.commit()
    return render(request, "auth/password_dimenticata.html", contesto)


@router.get("/{token}")
def pagina_nuova(request: Request, token: str, db: Db) -> Response:
    u = _utente_dal_token(db, token)
    return render(
        request, "auth/nuova_password.html", {"valido": u is not None, "u": u}, 200 if u else 404
    )


@router.post("/{token}")
def salva_nuova(
    request: Request,
    token: str,
    db: Db,
    nuova: Annotated[str, Form()],
    conferma: Annotated[str, Form()],
) -> Response:
    """Pagina pubblica: la protezione è il token segreto e monouso (e il controllo
    dell'origine delle richieste in app/main.py)."""
    u = _utente_dal_token(db, token)
    if u is None:
        return render(request, "auth/nuova_password.html", {"valido": False}, 404)
    errore = valida_nuova_password(nuova)
    if not errore and nuova != conferma:
        errore = "Le due password non coincidono."
    if errore:
        contesto = {"valido": True, "u": u, "errore": errore}
        return render(request, "auth/nuova_password.html", contesto, 400)
    u.password_hash = hash_password(nuova)
    u.recupero_hash = u.recupero_scade_il = None
    u.deve_cambiare_password = False
    u.tentativi_falliti = 0
    u.bloccato_fino_al = None
    elimina_sessioni_utente(db, u.id)
    audit.registra(db, None, "utente", u.id, "modifica", {"password": "password dimenticata"})
    db.commit()
    return RedirectResponse("/login?reimpostata=1", status_code=303)
