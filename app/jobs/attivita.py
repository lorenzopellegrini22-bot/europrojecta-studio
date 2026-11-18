"""I job pianificati del worker. Ognuno si può lanciare anche a mano dalla riga di comando
(python -m app.cli raccogli-bandi / pulizia)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db import get_sessionmaker
from app.models import Fonte, LogAccesso, Sessione
from app.sources.registro import FONTI
from app.sources.runner import esegui_raccolta

CONSERVAZIONE_LOG_ACCESSI_GIORNI = 365


def raccolta_bandi(forza: bool = False, solo: str | None = None) -> str:
    """Raccoglie i bandi da tutte le fonti attive. Una fonte che fallisce non ferma le altre."""
    righe = []
    fallite = []
    with get_sessionmaker()() as db:
        disattivate = set(db.scalars(select(Fonte.codice).where(Fonte.attiva.is_(False))))
        for codice, impl in FONTI.items():
            if (solo and codice != solo) or codice in disattivate:
                continue
            run = esegui_raccolta(db, impl, forza=forza)
            righe.append(f"{codice}: {run.esito} — {run.messaggio or ''}")
            if run.esito in ("errore", "fermato_sanita"):
                fallite.append(codice)
    riepilogo = "\n".join(righe) or "Nessuna fonte attiva."
    if fallite:
        # Il job risulta in errore (e lo vede l'email del mattino), ma le altre fonti sono
        # state comunque elaborate.
        raise ErroreRaccolta(riepilogo)
    return riepilogo


class ErroreRaccolta(Exception):
    """Almeno una fonte non è stata raccolta correttamente."""


def pulizia() -> str:
    """Elimina le sessioni scadute e i log degli accessi più vecchi di un anno."""
    ora = datetime.now(UTC)
    with get_sessionmaker()() as db:
        sessioni = db.execute(delete(Sessione).where(Sessione.scade_il < ora)).rowcount
        log = db.execute(
            delete(LogAccesso).where(
                LogAccesso.creato_il < ora - timedelta(days=CONSERVAZIONE_LOG_ACCESSI_GIORNI)
            )
        ).rowcount
        db.commit()
    return f"Eliminate {sessioni} sessioni scadute e {log} log di accesso vecchi."


GIORNI_PREAVVISO = 3


def testo_promemoria(nome: str, voci: list, oggi) -> str:  # type: ignore[no-untyped-def,type-arg]
    righe = [f"Buongiorno {nome},", "", "ecco le tue scadenze:", ""]
    for v in voci:
        quando = (
            f"scaduta da {(oggi - v.data).days} giorni"
            if v.data < oggi
            else ("oggi" if v.data == oggi else f"il {v.data:%d/%m/%Y}")
        )
        righe.append(
            f"- {v.titolo} · {v.pratica.azienda.ragione_sociale} "
            f"(pratica {v.pratica.codice}) · {quando}"
        )
    righe += ["", "Le trovi tutte nello Scadenzario di Europrojecta Studio."]
    return "\n".join(righe)


def promemoria_scadenze(oggi=None) -> str:  # type: ignore[no-untyped-def]
    """Ogni mattina: a ogni utente attivo l'elenco delle sue operazioni scadute o in scadenza
    entro GIORNI_PREAVVISO giorni. Al massimo un promemoria al giorno per utente; niente email
    se l'integrazione SMTP non è attiva o se non ci sono scadenze."""
    from datetime import date

    from app.comunicazioni import email_disponibile, invia_registrando
    from app.models import NotificaEmail, Utente
    from app.pratiche.servizio import scadenze

    oggi = oggi or date.today()
    inviati = saltati = errori = 0
    with get_sessionmaker()() as db:
        if not email_disponibile(db):
            return "Email non configurata o disattivata: nessun promemoria inviato."
        # "Uno al giorno": niente secondo promemoria se uno è partito nelle ultime 20 ore
        soglia = datetime.now(UTC) - timedelta(hours=20)
        for u in db.scalars(select(Utente).where(Utente.attivo)):
            gia = db.scalar(
                select(NotificaEmail.id).where(
                    NotificaEmail.tipo == "promemoria",
                    NotificaEmail.utente_id == u.id,
                    NotificaEmail.esito == "ok",
                    NotificaEmail.inviata_il >= soglia,
                )
            )
            if gia:
                saltati += 1
                continue
            voci = scadenze(
                db, oggi - timedelta(days=60), oggi + timedelta(days=GIORNI_PREAVVISO), u.id
            )
            if not voci:
                continue
            ok, _ = invia_registrando(
                db,
                tipo="promemoria",
                destinatario=u.email,
                oggetto=f"Scadenze: {len(voci)} da gestire",
                testo=testo_promemoria(u.nome, voci, oggi),
                utente=u,
            )
            if ok:
                inviati += 1
            else:
                errori += 1
    if errori:
        raise RuntimeError(f"Promemoria: {inviati} inviati, {errori} non riusciti.")
    return f"Promemoria inviati: {inviati} (già inviati oggi: {saltati})."


def raccolta_appalti() -> str:
    """Bandi di gara aperti da TED e iniziative aperte Consip (MEPA, SDAPA, convenzioni).
    Una fonte che fallisce non ferma l'altra; il job risulta in errore se ne fallisce una."""
    from app.appalti import consip, ted

    righe, errori = [], []
    with get_sessionmaker()() as db:
        for nome, funzione in (("TED", ted.raccogli), ("Consip", consip.raccogli)):
            try:
                esito = funzione(db)
                righe.append(esito[0] if isinstance(esito, tuple) else esito)
            except Exception as e:  # noqa: BLE001 — si registra e si prosegue
                db.rollback()
                errori.append(f"{nome}: {type(e).__name__}: {e}")
    if errori:
        raise RuntimeError(" | ".join(righe + errori))
    return " | ".join(righe)


def opportunita() -> str:
    """Ogni mattina: abbina clienti e bandi/appalti aperti, poi invia agli utenti attivi
    l'email con le opportunità nuove (se l'email è attiva)."""
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.models import Utente
    from app.opportunita.abbinamento import abbina, da_notificare, testo_email

    with get_sessionmaker()() as db:
        esito = abbina(db)
        nuove = da_notificare(db)
        if not nuove:
            return f"{esito} Nessuna email: niente di nuovo."
        if not email_disponibile(db):
            return f"{esito} Email non attiva: le novità sono nella pagina Opportunità."
        testo = testo_email(nuove)
        clienti = len({o.azienda_id for o in nuove})
        inviate = 0
        for u in db.scalars(select(Utente).where(Utente.attivo)):
            ok, _ = invia_registrando(
                db,
                tipo="opportunita",
                destinatario=u.email,
                oggetto=f"{len(nuove)} nuove opportunità per {clienti} clienti",
                testo=testo,
                utente=u,
            )
            inviate += ok
        ora = datetime.now(UTC)
        for o in nuove:
            o.notificata_il = ora
        db.commit()
        return f"{esito} Email inviate: {inviate}."
