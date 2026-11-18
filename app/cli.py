"""Comandi da riga di comando.

    uv run python -m app.cli crea-utente --email nome@studio.it --nome "Nome Cognome"
    uv run python -m app.cli crea-utente --email collab@studio.it --nome "..." --ruolo collaboratore
    uv run python -m app.cli reimposta-2fa --email nome@studio.it
    uv run python -m app.cli raccogli-bandi [--forza] [--fonte incentivi_italia]
    uv run python -m app.cli pulizia

Non esiste una registrazione pubblica: gli utenti si creano solo da qui.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth.password import hash_password, valida_nuova_password
from app.auth.sessioni import elimina_sessioni_utente
from app.db import get_sessionmaker
from app.models import Utente


def _chiedi_password() -> str:
    while True:
        p1 = getpass.getpass("Password: ")
        errore = valida_nuova_password(p1)
        if errore:
            print(errore)
            continue
        if p1 != getpass.getpass("Ripeti la password: "):
            print("Le password non coincidono.")
            continue
        return p1


def crea_utente(email: str, nome: str, ruolo: str) -> int:
    email = email.strip().lower()
    with get_sessionmaker()() as db:
        if db.scalar(select(Utente).where(Utente.email == email)):
            print(f"Esiste già un utente con email {email}.")
            return 1
        utente = Utente(
            email=email,
            nome=nome.strip(),
            password_hash=hash_password(_chiedi_password()),
            ruolo=ruolo,
        )
        db.add(utente)
        db.commit()
    print(f"Utente {email} creato ({ruolo}). Al primo accesso configurerà la 2FA.")
    return 0


def reimposta_2fa(email: str) -> int:
    """Per chi ha perso telefono e codici di recupero: al prossimo login riconfigura la 2FA."""
    email = email.strip().lower()
    with get_sessionmaker()() as db:
        utente = db.scalar(select(Utente).where(Utente.email == email))
        if utente is None:
            print(f"Nessun utente con email {email}.")
            return 1
        utente.totp_attivo = False
        utente.totp_secret_cifrato = None
        utente.totp_ultimo_intervallo = None
        utente.codici_recupero.clear()
        elimina_sessioni_utente(db, utente.id)
        db.commit()
    print(f"2FA azzerata per {email}: la riconfigurerà al prossimo accesso.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("crea-utente", help="crea un utente (la password viene chiesta)")
    p.add_argument("--email", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--ruolo", choices=["titolare", "collaboratore"], default="titolare")

    p = sub.add_parser("reimposta-2fa", help="azzera la 2FA di un utente")
    p.add_argument("--email", required=True)

    p = sub.add_parser("raccogli-bandi", help="esegue subito la raccolta dei bandi")
    p.add_argument(
        "--forza",
        action="store_true",
        help="riscarica anche se il file è invariato e ricalcola i requisiti di tutti i bandi",
    )
    p.add_argument("--fonte", help="solo questa fonte (codice)")

    sub.add_parser("pulizia", help="elimina sessioni scadute e log vecchi")
    sub.add_parser("modelli-esempio", help="crea i modelli di pratica di esempio mancanti")

    args = parser.parse_args(argv)
    if args.comando == "crea-utente":
        return crea_utente(args.email, args.nome, args.ruolo)
    if args.comando == "reimposta-2fa":
        return reimposta_2fa(args.email)
    if args.comando == "modelli-esempio":
        from app.db import get_sessionmaker
        from app.pratiche.modelli_esempio import crea_modelli_esempio

        with get_sessionmaker()() as db:
            creati = crea_modelli_esempio(db)
            db.commit()
        print(f"Modelli di esempio creati: {creati}")
        return 0

    import logging

    from app.jobs import attivita
    from app.jobs.esecuzione import esegui_job

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    if args.comando == "raccogli-bandi":
        esito = esegui_job(
            "raccolta_bandi", lambda: attivita.raccolta_bandi(forza=args.forza, solo=args.fonte)
        )
    else:
        esito = esegui_job("pulizia", attivita.pulizia)
    return 0 if esito == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
