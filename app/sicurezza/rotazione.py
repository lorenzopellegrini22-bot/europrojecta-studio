"""Rotazione di SECRET_KEY: ricifra tutti i segreti salvati con la chiave attuale.

Procedura (docs/messa-in-produzione.md):
1. SECRET_KEY = nuova chiave, SECRET_KEY_PRECEDENTI = vecchia chiave; riavvio.
2. `python -m app.cli ruota-chiavi`.
3. Si toglie SECRET_KEY_PRECEDENTI e si riavvia.
Le sessioni vengono chiuse (si rientra con password e 2FA) e i codici di recupero vanno
rigenerati: sono impronte con la chiave vecchia e non si possono ricalcolare.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.integrazioni.servizio import _scopo
from app.models import CodiceRecupero, Integrazione, Sessione, Utente
from app.sicurezza.cifratura import ErroreCifratura, ricifra

SCOPO_TOTP = "totp"


def ruota(db: Session) -> dict[str, int]:
    esito = {"totp": 0, "segreti_integrazioni": 0, "non_decifrabili": 0, "sessioni_chiuse": 0}
    for u in db.scalars(select(Utente).where(Utente.totp_secret_cifrato.is_not(None))):
        try:
            u.totp_secret_cifrato = ricifra(u.totp_secret_cifrato, SCOPO_TOTP)
            esito["totp"] += 1
        except ErroreCifratura:
            esito["non_decifrabili"] += 1
    for riga in db.scalars(select(Integrazione)):
        nuovi = dict(riga.segreti or {})
        for campo, token in nuovi.items():
            try:
                nuovi[campo] = ricifra(token, _scopo(riga.codice, campo))
                esito["segreti_integrazioni"] += 1
            except ErroreCifratura:
                esito["non_decifrabili"] += 1
        riga.segreti = nuovi
    esito["sessioni_chiuse"] = db.execute(delete(Sessione)).rowcount or 0
    esito["codici_recupero_da_rigenerare"] = len(list(db.scalars(select(CodiceRecupero.id))))
    db.commit()
    return esito
