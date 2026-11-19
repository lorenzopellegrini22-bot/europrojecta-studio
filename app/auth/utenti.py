"""Impostazioni → Utenti e permessi (solo titolare).

Il titolare crea le utenze dello studio, sceglie il ruolo e le sezioni che ognuno può usare,
le disattiva, azzera la 2FA o dà una nuova password temporanea. La password temporanea
compare una sola volta, sullo schermo del titolare: va comunicata a voce o di persona.
Al primo accesso l'utente configura la 2FA (obbligatoria) e conviene che cambi la password
da «Il mio account».
"""

import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.dipendenze import richiede_titolare, verifica_csrf
from app.auth.password import hash_password
from app.auth.permessi import PREDEFINITE_COLLABORATORE, RUOLI, SEZIONI, sezioni_di
from app.auth.sessioni import elimina_sessioni_utente
from app.comunicazioni import email_disponibile
from app.db import get_db
from app.models import Utente
from app.web import leggi_form, render, testo

router = APIRouter(prefix="/impostazioni/utenti")

Db = Annotated[Session, Depends(get_db)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def password_temporanea() -> str:
    """16 caratteri leggibili (niente 0/O, 1/l), sopra il minimo di 12."""
    alfabeto = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(16))


def _sezioni_dal_form(form: dict[str, Any]) -> list[str]:
    scelte = form.get("sezioni") or []
    scelte = [scelte] if isinstance(scelte, str) else scelte
    return [s for s in SEZIONI if s in scelte]


def _resta_un_titolare(db: Session, u: Utente) -> bool:
    """Togliendo a u il ruolo di titolare (o disattivandolo) resta almeno un titolare attivo?
    Le righe dei titolari si bloccano fino al commit: due titolari che si declassano a vicenda
    nello stesso momento non possono lasciare lo studio senza titolare."""
    if u.ruolo != "titolare" or not u.attivo:
        return True
    altri = db.scalars(
        select(Utente.id)
        .where(Utente.ruolo == "titolare", Utente.attivo, Utente.id != u.id)
        .with_for_update()
    ).all()
    return len(altri) > 0


def _contesto(**extra: Any) -> dict[str, Any]:
    return {
        "ruoli": RUOLI,
        "sezioni_disponibili": SEZIONI,
        "predefinite": PREDEFINITE_COLLABORATORE,
        **extra,
    }


@router.get("")
def elenco(request: Request, db: Db, _: Titolare) -> Response:
    utenti = list(db.scalars(select(Utente).order_by(Utente.attivo.desc(), Utente.nome)))
    return render(
        request,
        "impostazioni/utenti.html",
        _contesto(
            utenti=utenti,
            email_disponibile=email_disponibile(db),
            sezioni_utente={u.id: sezioni_di(u) for u in utenti},
            msg=request.query_params.get("msg"),
            esito=request.query_params.get("esito"),
        ),
    )


@router.post("", dependencies=CSRF)
def crea(request: Request, db: Db, titolare: Titolare, form: Form) -> Response:
    email = (testo(form, "email") or "").lower()
    nome = testo(form, "nome") or ""
    ruolo = testo(form, "ruolo") or "collaboratore"
    errori = []
    if not _EMAIL.match(email):
        errori.append("Email non valida.")
    elif db.scalar(select(Utente).where(Utente.email == email)):
        errori.append("Esiste già un utente con questa email.")
    if not nome:
        errori.append("Il nome è obbligatorio.")
    if ruolo not in RUOLI:
        errori.append("Ruolo non valido.")
    if errori:
        return RedirectResponse(
            "/impostazioni/utenti?esito=errore&msg=" + quote(" ".join(errori)), status_code=303
        )
    password = password_temporanea()
    u = Utente(
        email=email,
        nome=nome[:200],
        ruolo=ruolo,
        password_hash=hash_password(password),
        sezioni=None if ruolo == "titolare" else _sezioni_dal_form(form),
        attivo=True,
        deve_cambiare_password=True,
    )
    db.add(u)
    db.flush()
    audit.registra(
        db, titolare, "utente", u.id, "crea", {"email": email, "ruolo": ruolo, "sezioni": u.sezioni}
    )
    db.commit()
    if testo(form, "modo") == "invito":
        ok, messaggio = _invia_invito(db, u, titolare)
        if ok:
            return RedirectResponse(
                f"/impostazioni/utenti?esito=ok&msg={quote(f'Invito inviato a {email}.')}",
                status_code=303,
            )
        # Email non disponibile: si ripiega sulla password temporanea, mostrata qui
        return render(
            request,
            "impostazioni/utente_creato.html",
            {"u": u, "password": password, "nuovo": True, "avviso": messaggio},
        )
    # La password si mostra qui, una volta sola (niente redirect: non finisce in un URL)
    return render(
        request,
        "impostazioni/utente_creato.html",
        {"u": u, "password": password, "nuovo": True},
    )


def _invia_invito(db: Session, u: Utente, titolare: Utente) -> tuple[bool, str]:
    from app.auth.invito import crea_invito, testo_email
    from app.comunicazioni import invia_registrando

    link = crea_invito(u)
    db.commit()
    oggetto, corpo = testo_email(u, link, titolare)
    ok, messaggio = invia_registrando(
        db, tipo="avviso", destinatario=u.email, oggetto=oggetto, testo=corpo, utente=titolare
    )
    if not ok:
        u.invito_hash = u.invito_scade_il = None
        db.commit()
    audit.registra(db, titolare, "utente", u.id, "invia", {"invito": "ok" if ok else messaggio})
    db.commit()
    return ok, messaggio


@router.post("/{utente_id}", dependencies=CSRF)
def modifica(request: Request, utente_id: int, db: Db, titolare: Titolare, form: Form) -> Response:
    u = db.get(Utente, utente_id)
    if u is None:
        raise HTTPException(404)
    azione = testo(form, "azione") or "salva"
    stesso = u.id == titolare.id

    def torna(msg: str, esito: str = "ok") -> Response:
        return RedirectResponse(
            f"/impostazioni/utenti?esito={esito}&msg={quote(msg)}", status_code=303
        )

    if azione == "salva":
        ruolo = testo(form, "ruolo") or u.ruolo
        if ruolo not in RUOLI:
            return torna("Ruolo non valido.", "errore")
        if ruolo != "titolare" and not _resta_un_titolare(db, u):
            return torna("Serve almeno un titolare attivo.", "errore")
        prima = {"ruolo": u.ruolo, "sezioni": u.sezioni, "nome": u.nome}
        u.nome = (testo(form, "nome") or u.nome)[:200]
        u.ruolo = ruolo
        u.sezioni = None if ruolo == "titolare" else _sezioni_dal_form(form)
        audit.registra(
            db,
            titolare,
            "utente",
            u.id,
            "modifica",
            {"prima": prima, "dopo": {"ruolo": u.ruolo, "sezioni": u.sezioni, "nome": u.nome}},
        )
        db.commit()
        return torna(f"Permessi di {u.nome} salvati.")
    if azione in ("disattiva", "riattiva"):
        if stesso:
            return torna("Non puoi disattivare il tuo account.", "errore")
        if azione == "disattiva" and not _resta_un_titolare(db, u):
            return torna("Serve almeno un titolare attivo.", "errore")
        u.attivo = azione == "riattiva"
        if not u.attivo:
            elimina_sessioni_utente(db, u.id)
        audit.registra(db, titolare, "utente", u.id, "modifica", {"attivo": u.attivo})
        db.commit()
        return torna(f"{u.nome} {'riattivato' if u.attivo else 'disattivato'}.")
    if azione == "reimposta_2fa":
        u.totp_attivo = False
        u.totp_secret_cifrato = None
        u.totp_ultimo_intervallo = None
        u.codici_recupero.clear()
        elimina_sessioni_utente(db, u.id)
        audit.registra(db, titolare, "utente", u.id, "modifica", {"2fa": "azzerata"})
        db.commit()
        return torna(f"2FA di {u.nome} azzerata: la riconfigurerà al prossimo accesso.")
    if azione == "invito":
        ok, messaggio = _invia_invito(db, u, titolare)
        return torna(f"Invito inviato a {u.email}." if ok else messaggio, "ok" if ok else "errore")
    if azione == "nuova_password":
        password = password_temporanea()
        u.password_hash = hash_password(password)
        u.deve_cambiare_password = True
        u.tentativi_falliti = 0
        u.bloccato_fino_al = None
        elimina_sessioni_utente(db, u.id)
        audit.registra(
            db,
            titolare,
            "utente",
            u.id,
            "modifica",
            {"password": "nuova password temporanea", "il": datetime.now(UTC).isoformat()},
        )
        db.commit()
        return render(
            request,
            "impostazioni/utente_creato.html",
            {"u": u, "password": password, "nuovo": False},
        )
    raise HTTPException(400)
