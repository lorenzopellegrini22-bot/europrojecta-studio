"""Controllo che i job notturni girino davvero (non solo che una fonte risponda).

Problemi segnalati:
- il worker non dà segni di vita (battito ogni 15 minuti assente da oltre 45 minuti);
- un job non parte da più del previsto (es. la raccolta dei bandi da più di 26 ore);
- un job è bloccato «in corso» da più di 3 ore;
- l'ultima esecuzione di un job è fallita.

Dove si vedono: nella home del titolare, nell'email del mattino e in un'email immediata ai
titolari (una sola volta al giorno per lo stesso problema) inviata dal battito del worker.
Se il worker è fermo l'email immediata non può partire: per questo il problema compare
anche nella home, che è servita dall'app web.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import JobRun

BATTITO = "battito"
SILENZIO_WORKER = timedelta(minutes=45)
BLOCCATO_DOPO = timedelta(hours=3)
# job → ogni quanto deve partire al massimo (con margine)
ATTESI: dict[str, tuple[str, timedelta]] = {
    "raccolta_bandi": ("Raccolta dei bandi", timedelta(hours=26)),
    "raccolta_appalti": ("Raccolta degli appalti", timedelta(hours=26)),
    "documenti_bandi": ("Download dei PDF dei bandi", timedelta(hours=26)),
    "estrazione_bandi": ("Lettura dei bandi con l'AI", timedelta(hours=26)),
    "opportunita": ("Opportunità ed email del mattino", timedelta(hours=26)),
    "promemoria": ("Promemoria delle scadenze", timedelta(hours=26)),
    "pulizia": ("Pulizia notturna", timedelta(hours=26)),
    "riferimenti_istat": ("Tabelle ISTAT", timedelta(days=33)),
}


@dataclass
class Problema:
    job: str
    testo: str


def _con_cron() -> bool:
    """Installazioni senza worker (Vercel): i job partono da /cron, non c'è battito."""
    from app.config import get_settings

    return get_settings().cron_secret is not None


def registra_battito(db: Session) -> None:
    ora = datetime.now(UTC)
    db.add(JobRun(job=BATTITO, esito="ok", inizio=ora, fine=ora, messaggio="worker attivo"))
    # si tengono solo i battiti degli ultimi 2 giorni
    db.execute(delete(JobRun).where(JobRun.job == BATTITO, JobRun.inizio < ora - timedelta(days=2)))
    db.commit()


def _quando(t: datetime) -> str:
    from zoneinfo import ZoneInfo

    from app.config import get_settings

    return t.astimezone(ZoneInfo(get_settings().fuso_orario)).strftime("%d/%m %H:%M")


def problemi(db: Session, ora: datetime | None = None) -> list[Problema]:
    ora = ora or datetime.now(UTC)
    elenco: list[Problema] = []
    ultimo_battito = db.scalar(select(func.max(JobRun.inizio)).where(JobRun.job == BATTITO))
    primo_avvio = db.scalar(select(func.min(JobRun.inizio)))
    if _con_cron():
        pass  # nessun worker (Vercel Cron chiama /cron/<job>): contano solo i job
    elif ultimo_battito is None:
        if primo_avvio is not None and ora - primo_avvio > SILENZIO_WORKER:
            elenco.append(Problema(BATTITO, "Il worker dei job notturni non risulta avviato."))
    elif ora - ultimo_battito > SILENZIO_WORKER:
        elenco.append(
            Problema(
                BATTITO,
                f"Il worker dei job notturni non dà segni di vita dal {_quando(ultimo_battito)}: "
                "raccolte, analisi AI ed email del mattino sono ferme.",
            )
        )
    for job, (nome, ogni) in ATTESI.items():
        ultimo = db.scalar(
            select(JobRun).where(JobRun.job == job).order_by(JobRun.inizio.desc()).limit(1)
        )
        if ultimo is None:
            if primo_avvio is not None and ora - primo_avvio > ogni:
                elenco.append(Problema(job, f"{nome}: non è mai partita."))
            continue
        if ultimo.esito == "in_corso" and ora - ultimo.inizio > BLOCCATO_DOPO:
            elenco.append(
                Problema(job, f"{nome}: bloccata dal {_quando(ultimo.inizio)} (ancora in corso).")
            )
        elif ora - ultimo.inizio > ogni:
            elenco.append(Problema(job, f"{nome}: non parte dal {_quando(ultimo.inizio)}."))
        elif ultimo.esito == "errore":
            dettaglio = (ultimo.messaggio or "").split("\n")[0][:160]
            elenco.append(
                Problema(job, f"{nome}: fallita il {_quando(ultimo.inizio)} ({dettaglio}).")
            )
    return elenco


def avvisa(db: Session) -> int:
    """Email immediata ai titolari per i problemi nuovi (una volta al giorno per problema)."""
    from app.comunicazioni import email_disponibile, invia_registrando
    from app.models import NotificaEmail, Utente

    elenco = problemi(db)
    if not elenco or not email_disponibile(db):
        return 0
    da = datetime.now(UTC) - timedelta(hours=24)
    inviate = 0
    for p in elenco:
        oggetto = f"Attenzione: job notturno — {p.testo.split(':')[0]}"
        gia = db.scalar(
            select(NotificaEmail.id).where(
                NotificaEmail.tipo == "avviso",
                NotificaEmail.oggetto == oggetto,
                NotificaEmail.inviata_il >= da,
            )
        )
        if gia:
            continue
        for u in db.scalars(select(Utente).where(Utente.attivo, Utente.ruolo == "titolare")):
            ok, _ = invia_registrando(
                db,
                tipo="avviso",
                destinatario=u.email,
                oggetto=oggetto,
                testo=f"{p.testo}\n\nDettagli nella home di Europrojecta Studio e nella pagina "
                "Fonti dei bandi.",
                utente=u,
            )
            inviate += ok
    return inviate
