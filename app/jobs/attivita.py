"""I job pianificati del worker. Ognuno si può lanciare anche a mano dalla riga di comando
(python -m app.cli raccogli-bandi / pulizia)."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db import get_sessionmaker
from app.models import Fonte, LogAccesso, Sessione
from app.sources.registro import FONTI
from app.sources.runner import esegui_raccolta

log = logging.getLogger(__name__)
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
    from app.bandi.manuali import aggiorna_stati as stati_manuali

    with get_sessionmaker()() as db:
        cambiati = stati_manuali(db)
        db.commit()
    if cambiati:
        riepilogo += f"\nBandi inseriti dallo studio: {cambiati} cambiati di stato."
    try:
        from app.bandi.doppioni import collega

        with get_sessionmaker()() as db:
            riepilogo += "\n" + collega(db)
    except Exception:  # i doppioni non devono far fallire la raccolta
        log.exception("Collegamento dei bandi doppi non riuscito")
    if fallite:
        try:
            avviso_fonti()
        except Exception:
            log.exception("Avviso delle fonti non riuscito")
    if fallite:
        # Il job risulta in errore (e lo vede l'email del mattino), ma le altre fonti sono
        # state comunque elaborate.
        raise ErroreRaccolta(riepilogo)
    return riepilogo


def riferimenti_istat() -> str:
    """Ogni mese: ricarica comuni e raccordo ATECO dai file ufficiali ISTAT, poi riconosce
    il comune delle sedi che non hanno ancora il codice ISTAT."""
    from app.models import Sede
    from app.riferimenti import istat, territorio

    with get_sessionmaker()() as db:
        esito = istat.aggiorna(db)
        t = territorio.in_memoria(db)
        riconosciute = sum(
            territorio.completa_sede(t, s)
            for s in db.scalars(select(Sede).where(Sede.comune_testo.is_not(None)))
        )
        db.commit()
    return f"{esito} Sedi con comune riconosciuto: {riconosciute}."


def avviso_fonti() -> int:
    """Email ai titolari quando una fonte arriva a N fallimenti consecutivi (brief: default 2).
    Parte una volta sola, quando il contatore raggiunge la soglia."""
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.config import get_settings
    from app.models import Utente

    soglia = get_settings().avviso_fonte_dopo
    with get_sessionmaker()() as db:
        fonti = list(db.scalars(select(Fonte).where(Fonte.fallimenti_consecutivi == soglia)))
        if not fonti or not email_disponibile(db):
            return 0
        testo = "\n".join(
            [
                "Buongiorno,",
                "",
                f"queste fonti dei bandi non rispondono da {soglia} raccolte consecutive:",
                *[f"• {f.nome} ({f.url})" for f in fonti],
                "",
                "I bandi già raccolti restano visibili. Dettagli nella pagina Fonti dei bandi.",
            ]
        )
        inviate = 0
        for u in db.scalars(select(Utente).where(Utente.attivo, Utente.ruolo == "titolare")):
            ok, _ = invia_registrando(
                db,
                tipo="avviso",
                destinatario=u.email,
                oggetto=f"Attenzione: {len(fonti)} fonti dei bandi non rispondono",
                testo=testo,
                utente=u,
            )
            inviate += ok
        return inviate


class ErroreRaccolta(Exception):
    """Almeno una fonte non è stata raccolta correttamente."""


def backup_esterno() -> str:
    """Backup cifrato su storage S3 (Vercel e simili): vedi app/backup_esterno.py."""
    from app.backup_esterno import esegui

    return esegui()


def pulizia() -> str:
    """Elimina le sessioni scadute, i log degli accessi più vecchi di un anno e quello che
    è nel cestino da più di 30 giorni."""
    ora = datetime.now(UTC)
    with get_sessionmaker()() as db:
        sessioni = db.execute(delete(Sessione).where(Sessione.scade_il < ora)).rowcount
        log = db.execute(
            delete(LogAccesso).where(
                LogAccesso.creato_il < ora - timedelta(days=CONSERVAZIONE_LOG_ACCESSI_GIORNI)
            )
        ).rowcount
        from app.cestino.servizio import svuota_scaduti

        cestino = svuota_scaduti(db, ora)
        db.commit()
    return (
        f"Eliminate {sessioni} sessioni scadute, {log} log di accesso vecchi e "
        f"{cestino} elementi rimasti nel cestino oltre 30 giorni."
    )


GIORNI_PREAVVISO = 3


def testo_promemoria(nome: str, voci: list, oggi) -> str:  # type: ignore[no-untyped-def,type-arg]
    righe = [f"Buongiorno {nome},", "", "ecco le tue scadenze:", ""]
    for v in voci:
        quando = (
            f"scaduta da {(oggi - v.data).days} giorni"
            if v.data < oggi
            else ("oggi" if v.data == oggi else f"il {v.data:%d/%m/%Y}")
        )
        if v.pratica is not None:
            dove = f"{v.pratica.azienda.ragione_sociale} (pratica {v.pratica.codice})"
        else:
            dove = v.promemoria.azienda.ragione_sociale
        righe.append(f"- {v.titolo} · {dove} · {quando}")
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
        from app.appalti import documenti

        for nome, funzione in (
            ("TED", ted.raccogli),
            ("Consip", consip.raccogli),
            ("Documentazione delle gare", documenti.aggiorna),
        ):
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
    """Ogni mattina (pipeline del brief, passi 6-7 ed email delle 7:00): abbinamento a regole,
    punteggio AI, bozze periodiche degli aggiornamenti ai clienti, poi l'email del mattino a
    ogni utente attivo. L'email parte anche senza novità; al massimo una al giorno per utente.
    Un passo che fallisce non blocca i successivi."""
    from app.aggiornamenti.servizio import bozze_periodiche
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.config import get_settings
    from app.extraction.servizio import impostazioni
    from app.models import NotificaEmail, Utente
    from app.notifiche import componi
    from app.opportunita.abbinamento import abbina, da_notificare
    from app.opportunita.valutazione import esegui as valuta
    from app.squadra.seguiti import controlla as controlla_seguiti

    with get_sessionmaker()() as db:
        esiti = [abbina(db)]
        for nome, passo in (
            ("Valutazione AI", valuta),
            ("Bozze aggiornamenti", bozze_periodiche),
            ("Bandi e gare seguiti", controlla_seguiti),
        ):
            try:
                esiti.append(passo(db))
            except Exception as e:  # un passo non deve bloccare l'email del mattino
                db.rollback()
                log.exception("%s non riuscita", nome)
                esiti.append(f"{nome} non riuscita: {type(e).__name__}.")
        esito = " ".join(esiti)
        nuove = da_notificare(db, impostazioni(db).soglia_notifica)
        if not email_disponibile(db):
            return f"{esito} Email non attiva: le novità sono nella pagina Opportunità."
        base_url = get_settings().url_pubblico.rstrip("/")
        soglia = datetime.now(UTC) - timedelta(hours=20)
        inviate = 0
        for u in db.scalars(select(Utente).where(Utente.attivo)):
            gia = db.scalar(
                select(NotificaEmail.id).where(
                    NotificaEmail.tipo == "riepilogo_mattutino",
                    NotificaEmail.utente_id == u.id,
                    NotificaEmail.esito == "ok",
                    NotificaEmail.inviata_il >= soglia,
                )
            )
            if gia:
                continue
            oggetto, testo = componi(db, u, nuove, base_url)
            ok, _ = invia_registrando(
                db,
                tipo="riepilogo_mattutino",
                destinatario=u.email,
                oggetto=oggetto,
                testo=testo,
                utente=u,
            )
            inviate += ok
        ora = datetime.now(UTC)
        for o in nuove:
            o.notificata_il = ora
        db.commit()
        return f"{esito} Email inviate: {inviate}."


def estrazione_bandi() -> str:
    """Ogni notte: l'AI struttura i requisiti dei bandi aperti o in arrivo nuovi o cambiati."""
    from app.extraction.servizio import esegui

    with get_sessionmaker()() as db:
        return esegui(db)


def documenti_bandi() -> str:
    """Ogni notte, prima dell'estrazione: scarica e legge i PDF dei bandi attivi."""
    from app.documenti.servizio import esegui

    with get_sessionmaker()() as db:
        return esegui(db)
