# ruff: noqa: E501
"""Calendario personale in formato iCalendar (.ics), da aggiungere a Outlook, Google Calendar o
al calendario del telefono «da indirizzo internet»: il calendario lo rilegge da solo.

Contiene le scadenze dell'utente (operazioni assegnate e pratiche di cui è responsabile; per il
titolare tutte quelle dello studio) e le chiusure dei bandi e delle gare che segue.
Il link contiene un token segreto (nel database solo la sua impronta); si può rigenerare, e il
vecchio smette di funzionare.
"""

import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appalto, Bando, Seguito, Utente
from app.sicurezza.cifratura import impronta

SCOPO = "calendario_utente"
GIORNI_PRIMA = 30
GIORNI_DOPO = 365


def crea_link(utente: Utente) -> str:
    token = secrets.token_urlsafe(32)
    utente.calendario_hash = impronta(token, SCOPO)
    return link(token)


def link(token: str) -> str:
    return f"{get_settings().url_pubblico.rstrip('/')}/calendario/{token}.ics"


def utente_dal_token(db: Session, token: str) -> Utente | None:
    if not token or len(token) > 100:
        return None
    u = db.scalar(select(Utente).where(Utente.calendario_hash == impronta(token, SCOPO)))
    return u if u is not None and u.attivo else None


def _testo(s: str) -> str:
    # niente ritorni a capo «nudi» né caratteri di controllo: non devono creare nuove righe
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(c for c in s if c == "\n" or c >= " ")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _piega(riga: str) -> str:
    """Righe al massimo di 75 byte (RFC 5545), le successive iniziano con uno spazio."""
    dati = riga.encode("utf-8")
    if len(dati) <= 75:
        return riga
    parti, inizio, limite = [], 0, 75
    while inizio < len(dati):
        fine = min(inizio + limite, len(dati))
        while fine < len(dati) and (dati[fine] & 0xC0) == 0x80:  # non spezzare un carattere
            fine -= 1
        parti.append(dati[inizio:fine].decode("utf-8"))
        inizio, limite = fine, 74
    return "\r\n ".join(parti)


def _evento(
    uid: str, giorno: date, titolo: str, descrizione: str, url: str, adesso: str
) -> list[str]:
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@europrojecta-studio",
        f"DTSTAMP:{adesso}",
        f"DTSTART;VALUE=DATE:{giorno:%Y%m%d}",
        f"DTEND;VALUE=DATE:{giorno + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_testo(titolo)}",
        f"DESCRIPTION:{_testo(descrizione)}",
        f"URL:{url}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def genera(db: Session, utente: Utente, oggi: date | None = None) -> str:
    from app.pratiche.servizio import scadenze

    oggi = oggi or date.today()
    base = get_settings().url_pubblico.rstrip("/")
    adesso = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    righe = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Europrojecta Studio//Scadenze//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_testo('Europrojecta · ' + utente.nome)}",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]
    dal, al = oggi - timedelta(days=GIORNI_PRIMA), oggi + timedelta(days=GIORNI_DOPO)
    from app.auth.permessi import puo_vedere

    # solo ciò che l'utente può ancora vedere: se gli si tolgono le sezioni, il calendario si svuota
    voci = (
        scadenze(db, dal, al, None if utente.is_titolare else utente.id)
        if puo_vedere(utente, "/pratiche")
        else []
    )
    for v in voci:
        if v.pratica is not None:
            cliente = v.pratica.azienda.ragione_sociale if v.pratica.azienda else ""
            titolo = f"{v.titolo} – {cliente}" if cliente else v.titolo
            descrizione = f"Pratica {v.pratica.codice}: {v.pratica.nome_bando}"
            if v.fase is not None:
                descrizione += f"\nFase: {v.fase.nome}"
            if v.assegnata_a is not None:
                descrizione += f"\nA cura di: {v.assegnata_a.nome}"
            url = f"{base}/pratiche/{v.pratica.id}"
            uid = f"{v.tipo}-{v.operazione.id if v.operazione else v.pratica.id}"
        else:
            sc = v.promemoria
            cliente = sc.azienda.ragione_sociale if sc.azienda else ""
            titolo = f"{v.titolo} – {cliente}" if cliente else v.titolo
            descrizione = "Promemoria del cliente"
            url = f"{base}/clienti/{sc.azienda_id}" if sc.azienda_id else f"{base}/scadenzario"
            uid = f"promemoria-{sc.id}"
        righe += _evento(uid, v.data, titolo, descrizione, url, adesso)
    from app.squadra.seguiti import visibili

    for s in db.scalars(select(Seguito).where(Seguito.utente_id == utente.id, visibili(utente))):
        if s.bando_id:
            b = db.get(Bando, s.bando_id)
            if b and b.data_chiusura and dal <= b.data_chiusura <= al:
                righe += _evento(
                    f"bando-{b.id}-u{utente.id}", b.data_chiusura, f"Chiude il bando: {b.titolo[:120]}",
                    b.ente or "", f"{base}/bandi/{b.id}", adesso,
                )  # fmt: skip
        else:
            a = db.get(Appalto, s.appalto_id)
            if a and a.scadenza and dal <= a.scadenza <= al:
                righe += _evento(
                    f"gara-{a.id}-u{utente.id}", a.scadenza, f"Scade la gara: {a.titolo[:120]}",
                    a.stazione_appaltante or "", f"{base}/appalti/{a.id}", adesso,
                )  # fmt: skip
    righe.append("END:VCALENDAR")
    return "\r\n".join(_piega(r) for r in righe) + "\r\n"
