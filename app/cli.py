"""Comandi da riga di comando.

    uv run python -m app.cli crea-utente --email nome@studio.it --nome "Nome Cognome"
    uv run python -m app.cli crea-utente --email collab@studio.it --nome "..." --ruolo collaboratore
    uv run python -m app.cli reimposta-2fa --email nome@studio.it
    uv run python -m app.cli reimposta-password --email nome@studio.it
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


def reimposta_password(email: str) -> int:
    """Senza email configurata il link di «Password dimenticata» non può partire: dal server
    il titolare sceglie una nuova password (chiesta a schermo, mai come argomento)."""
    email = email.strip().lower()
    with get_sessionmaker()() as db:
        utente = db.scalar(select(Utente).where(Utente.email == email))
        if utente is None:
            print(f"Nessun utente con email {email}.")
            return 1
        utente.password_hash = hash_password(_chiedi_password())
        utente.tentativi_falliti = 0
        utente.bloccato_fino_al = None
        utente.recupero_hash = utente.recupero_scade_il = None
        elimina_sessioni_utente(db, utente.id)
        db.commit()
    print(f"Password cambiata per {email}: tutte le sue sessioni sono state chiuse.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("crea-utente", help="crea un utente (la password viene chiesta)")
    p.add_argument("--email", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--ruolo", choices=["titolare", "collaboratore", "lettura"], default="titolare")

    p = sub.add_parser("reimposta-password", help="nuova password (viene chiesta)")
    p.add_argument("--email", required=True)
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
    sub.add_parser("promemoria", help="invia subito i promemoria delle scadenze")
    sub.add_parser("raccogli-appalti", help="raccoglie subito i bandi di gara da TED")
    sub.add_parser("opportunita", help="abbina subito clienti e bandi/appalti")
    sub.add_parser("estrai-bandi", help="struttura subito i requisiti dei bandi con l'AI")
    sub.add_parser("documenti-bandi", help="scarica subito i PDF dei bandi attivi")

    sub.add_parser("collega-doppioni", help="collega i bandi doppi tra fonte nazionale e regionali")

    sub.add_parser("ruota-chiavi", help="ricifra i segreti con la SECRET_KEY attuale")
    sub.add_parser("carica-istat", help="scarica e carica comuni e raccordo ATECO (ISTAT)")
    p = sub.add_parser(
        "collaudo-prepara", help="prepara il file del collaudo (20 bandi, 10 aziende)"
    )
    p.add_argument("--file", required=True)
    p = sub.add_parser("collaudo", help="esegue il collaudo del matching e scrive il report")
    p.add_argument("--file", required=True)
    p.add_argument("--report", default="collaudo.md")
    sub.add_parser("backup-ora", help="backup cifrato subito sullo storage S3 (BACKUP_S3_*)")
    p = sub.add_parser("backup-decifra", help="apre un backup cifrato (serve BACKUP_PASSPHRASE)")
    p.add_argument("--file", required=True, help="il file .zip.cifrato scaricato dallo storage")
    p.add_argument("--uscita", required=True, help="dove scrivere lo zip in chiaro")

    args = parser.parse_args(argv)
    if args.comando == "backup-decifra":
        from pathlib import Path

        from app.backup_esterno import ErroreBackup, decifra_file
        from app.config import get_settings

        passphrase = get_settings().backup_passphrase
        if passphrase is None:
            print("Imposta BACKUP_PASSPHRASE (la passphrase dei backup).")
            return 1
        try:
            decifra_file(Path(args.file), Path(args.uscita), passphrase.get_secret_value())
        except ErroreBackup as e:
            Path(args.uscita).unlink(missing_ok=True)
            print(e)
            return 1
        print(f"Backup aperto: {args.uscita}")
        return 0
    if args.comando in ("collaudo-prepara", "collaudo"):
        import json
        from pathlib import Path

        from app import collaudo
        from app.db import get_sessionmaker

        with get_sessionmaker()() as db:
            if args.comando == "collaudo-prepara":
                dati = collaudo.prepara(db)
                Path(args.file).write_text(
                    json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                n_bandi, n_coppie = len(dati["bandi"]), len(dati["etichette"])
                print(f"Scritto {args.file}: {n_bandi} bandi, {n_coppie} coppie da etichettare.")
                return 0
            risultato = collaudo.esegui(db, collaudo.leggi_file(args.file))
        Path(args.report).write_text(collaudo.report(risultato), encoding="utf-8")
        esito = "SUPERATO" if risultato.superato else "NON SUPERATO"
        print(f"Report scritto in {args.report}: {esito}")
        return 0 if risultato.superato else 1
    if args.comando == "carica-istat":
        from app.jobs import attivita as job

        print(job.riferimenti_istat())
        return 0
    if args.comando == "ruota-chiavi":
        from app.db import get_sessionmaker
        from app.sicurezza.rotazione import ruota

        with get_sessionmaker()() as db:
            esito = ruota(db)
        for chiave, valore in esito.items():
            print(f"{chiave}: {valore}")
        if esito["non_decifrabili"]:
            print("ATTENZIONE: alcuni segreti non si decifrano con nessuna chiave: reinseriscili.")
        print("Ora togli SECRET_KEY_PRECEDENTI, riavvia, e rigenera i codici di recupero.")
        return 1 if esito["non_decifrabili"] else 0
    if args.comando == "collega-doppioni":
        from app.bandi.doppioni import collega
        from app.db import get_sessionmaker

        with get_sessionmaker()() as db:
            print(collega(db))
        return 0
    if args.comando == "crea-utente":
        return crea_utente(args.email, args.nome, args.ruolo)
    if args.comando == "reimposta-2fa":
        return reimposta_2fa(args.email)
    if args.comando == "reimposta-password":
        return reimposta_password(args.email)
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
    elif args.comando == "documenti-bandi":
        esito = esegui_job("documenti_bandi", attivita.documenti_bandi)
    elif args.comando == "estrai-bandi":
        esito = esegui_job("estrazione_bandi", attivita.estrazione_bandi)
    elif args.comando == "opportunita":
        esito = esegui_job("opportunita", attivita.opportunita)
    elif args.comando == "raccogli-appalti":
        esito = esegui_job("raccolta_appalti", attivita.raccolta_appalti)
    elif args.comando == "promemoria":
        esito = esegui_job("promemoria", attivita.promemoria_scadenze)
    elif args.comando == "backup-ora":
        esito = esegui_job("backup_esterno", attivita.backup_esterno)
    else:
        esito = esegui_job("pulizia", attivita.pulizia)
    return 0 if esito == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
