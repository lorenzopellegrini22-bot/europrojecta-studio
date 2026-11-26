"""Automazioni: messaggi ai clienti che partono da soli quando succede qualcosa.

Ogni automazione (tabella `automazione`, pagina /automazioni) dice:
- QUANDO: nuovo bando adatto a un cliente, bando adatto in scadenza tra N giorni, riepilogo
  periodico (settimanale o mensile);
- PER CHI: tutti i clienti, solo imprese, solo enti pubblici o clienti scelti; quali tipi di
  bando (europei, nazionali, regionali e locali, gare) e da quale punteggio;
- COSA FA: prepara una bozza da approvare in Aggiornamenti oppure invia da sola;
- IL TESTO: oggetto e messaggio con segnaposto.

Sicurezze uguali per tutte (oltre a consenso e disiscrizione, controllati da
app.aggiornamenti.servizio):
- al massimo un messaggio al giorno per cliente dalle automazioni: più bandi nello stesso
  giorno vanno nello stesso messaggio, gli altri il giorno dopo;
- un bando già citato a quel cliente non riparte come «nuovo» (il promemoria di scadenza sì,
  una volta per automazione);
- una nuova automazione non manda gli arretrati: conta solo quello che trova dopo l'attivazione.

Girano ogni mattina dopo l'abbinamento e la valutazione AI (app/jobs/attivita.py).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.aggiornamenti import servizio as agg
from app.models import AggiornamentoCliente, Automazione, Azienda, Opportunita, Utente

log = logging.getLogger(__name__)

EVENTI = {
    "nuovo_bando": "Esce un nuovo bando adatto",
    "scadenza": "Un bando adatto sta per scadere",
    "riepilogo": "Riepilogo periodico",
}
DESTINATARI = {
    "tutti": "Tutti i clienti",
    "imprese": "Solo le imprese",
    "enti": "Solo enti pubblici e comuni",
    "scelti": "Clienti scelti",
}
AZIONI = {
    "bozza": "Prepara una bozza da approvare",
    "invio": "Invia da sola",
}
CANALI = {
    "preferito": "Quello preferito dal referente",
    "email": "Email",
    "whatsapp": "WhatsApp",
}
AMBITI = {
    "ue": "Bandi europei",
    "nazionali": "Bandi nazionali",
    "locali": "Bandi regionali e locali",
    "appalti": "Gare d'appalto",
}
FREQUENZE = {"settimanale": 7, "mensile": 30}
SEGNAPOSTO = {
    "{nome_referente}": "nome del referente",
    "{ragione_sociale}": "nome del cliente (azienda o ente)",
    "{titolo_bando}": "titolo del bando (con più bandi: il primo «e un altro bando»)",
    "{scadenza}": "scadenza del bando",
    "{perche}": "perché è adatto al cliente",
    "{link_bando}": "link alla pagina ufficiale",
    "{elenco_bandi}": "tutti i bandi del messaggio, con scadenza, motivo e link",
    "{riepilogo}": "solo nel riepilogo: bandi del periodo, pratiche e scadenze",
    "{nome_consulente}": "il tuo nome",
}
# Sostituiti solo al momento dell'invio (app.aggiornamenti.servizio.testo_finale): così
# all'AI e nelle bozze restano segnaposto, mai i nomi
PERSONALI = ("{nome_referente}", "{ragione_sociale}", "{nome_consulente}")
STATI_APERTI = ("nuova", "vista", "interessante")
MASSIMO_BANDI = 5

PREDEFINITE: list[dict[str, Any]] = [
    {
        "nome": "Nuovo bando adatto",
        "evento": "nuovo_bando",
        "punteggio_minimo": 70,
        "oggetto": "Nuovo bando per {ragione_sociale}: {titolo_bando}",
        "testo": (
            "Buongiorno {nome_referente},\n\n"
            "ci sono novità tra i bandi che possono interessare {ragione_sociale}:\n\n"
            "{elenco_bandi}\n\n"
            "Se volete approfondire possiamo sentirci per valutare insieme requisiti e tempi.\n\n"
            "{nome_consulente}"
        ),
    },
    {
        "nome": "Promemoria: il bando scade tra 15 giorni",
        "evento": "scadenza",
        "giorni_scadenza": 15,
        "punteggio_minimo": 70,
        "oggetto": "Promemoria: {titolo_bando} scade il {scadenza}",
        "testo": (
            "Buongiorno {nome_referente},\n\n"
            "vi ricordo che questo bando, adatto a {ragione_sociale}, scade a breve:\n\n"
            "{elenco_bandi}\n\n"
            "Se pensate di partecipare è il momento di preparare la domanda: sono a "
            "disposizione.\n\n"
            "{nome_consulente}"
        ),
    },
]
TESTO_RIEPILOGO = {
    "oggetto": "Aggiornamento su bandi e pratiche per {ragione_sociale}",
    "testo": (
        "Buongiorno {nome_referente},\n\n{riepilogo}\n\n"
        "Per qualsiasi domanda sono a disposizione.\n{nome_consulente}"
    ),
}
PROMPT_AI = (
    "Riscrivi in italiano semplice e cordiale il messaggio che un consulente di finanza "
    "agevolata manda a un cliente (azienda o ente pubblico). Mantieni ESATTAMENTE i segnaposto "
    "tra parentesi graffe e tutti i fatti (titoli dei bandi, scadenze, link). Mantieni saluto e "
    "firma. Non aggiungere informazioni, promesse o nomi. Massimo 1500 caratteri. Rispondi "
    "solo con il testo."
)


# --- Chi e cosa ---------------------------------------------------------------------------


def clienti(db: Session, au: Automazione) -> list[Azienda]:
    q = (
        select(Azienda)
        .where(Azienda.stato_cliente != "ex")
        .options(selectinload(Azienda.referenti))
        .order_by(Azienda.ragione_sociale)
    )
    if au.destinatari == "imprese":
        q = q.where(or_(Azienda.tipo_soggetto == "impresa", Azienda.tipo_soggetto.is_(None)))
    elif au.destinatari == "enti":
        q = q.where(Azienda.tipo_soggetto == "ente_pubblico")
    elif au.destinatari == "scelti":
        q = q.where(Azienda.id.in_(au.aziende_ids or [-1]))
    return list(db.scalars(q))


def _ambito(o: Opportunita) -> str:
    from app.opportunita.abbinamento import ambito_bando

    return "appalti" if o.appalto_id else ambito_bando(o.bando)


def _citati(db: Session, a: Azienda, au: Automazione) -> tuple[set[int], set[int]]:
    """Bandi e gare da non ripetere: per «nuovo bando» quelli già in un messaggio al cliente
    (anche a mano), per il promemoria quelli già ricordati da questa automazione."""
    q = select(AggiornamentoCliente.bandi_citati, AggiornamentoCliente.appalti_citati).where(
        AggiornamentoCliente.azienda_id == a.id
    )
    if au.evento == "scadenza":
        q = q.where(AggiornamentoCliente.automazione_id == au.id)
    else:
        q = q.where(
            or_(
                AggiornamentoCliente.automazione_id == au.id,
                AggiornamentoCliente.stato != "scartato",
            )
        )
    bandi: set[int] = set()
    gare: set[int] = set()
    for b, g in db.execute(q):
        bandi.update(b or [])
        gare.update(g or [])
    return bandi, gare


def opportunita_per(
    db: Session, a: Azienda, au: Automazione, oggi: date, anteprima: bool = False
) -> list[Opportunita]:
    """Bandi e gare da mettere nel messaggio per questo cliente. Con anteprima=True ignora
    data di attivazione e messaggi già inviati (serve a vedere com'è il testo)."""
    q = (
        select(Opportunita)
        .where(
            Opportunita.azienda_id == a.id,
            Opportunita.punteggio >= au.punteggio_minimo,
            Opportunita.stato.in_(STATI_APERTI),
            or_(
                Opportunita.feedback.is_(None),
                Opportunita.feedback.not_in(("non_pertinente", "gia_noto")),
            ),
        )
        .options(selectinload(Opportunita.bando), selectinload(Opportunita.appalto))
        .order_by(Opportunita.punteggio.desc())
    )
    if au.evento == "nuovo_bando" and au.attiva_dal and not anteprima:
        q = q.where(Opportunita.trovata_il >= au.attiva_dal)
    bandi, gare = (set(), set()) if anteprima else _citati(db, a, au)
    ambiti = set(au.ambiti) if au.ambiti else set(AMBITI)
    scelte = []
    for o in db.scalars(q):
        if (o.bando_id in bandi) or (o.appalto_id in gare) or _ambito(o) not in ambiti:
            continue
        if o.bando is not None and o.bando.stato not in ("aperto", "in_arrivo"):
            continue
        fine = o.scadenza
        if fine is not None and fine < oggi:
            continue
        if au.evento == "scadenza":
            limite = oggi + timedelta(days=au.giorni_scadenza or 15)
            if fine is None or fine > limite:
                continue
        scelte.append(o)
    return scelte[:MASSIMO_BANDI]


# --- Testo --------------------------------------------------------------------------------


def _motivo(o: Opportunita) -> str:
    return o.motivazione_ai or "; ".join((o.motivi or {}).get("soddisfatti", [])[:2])


def _link(o: Opportunita) -> str:
    if o.bando is not None:
        return o.bando.link_ufficiale or ""
    return (o.appalto.link or "") if o.appalto else ""


def elenco(opportunita: list[Opportunita]) -> str:
    righe = []
    for o in opportunita:
        scad = f" (scadenza {o.scadenza:%d/%m/%Y})" if o.scadenza else ""
        righe.append(f"• {o.titolo}{scad}")
        if motivo := _motivo(o):
            righe.append(f"  Perché: {motivo}")
        if link := _link(o):
            righe.append(f"  {link}")
    return "\n".join(righe)


def riepilogo(dati: dict[str, Any]) -> str:
    """Corpo del riepilogo periodico (bandi, pratiche, scadenze) senza saluto né firma."""
    testa, coda = agg.parti_bozza(dati)
    righe_testa = testa.split("\n")[2:]  # via «Buongiorno {nome_referente},» e la riga vuota
    righe_coda = coda.split("\n")[:-2]  # via «Per qualsiasi domanda…» e la firma
    return "\n".join(righe_testa + ([""] + righe_coda if any(righe_coda) else [])).strip()


def compila(modello: str, opportunita: list[Opportunita], extra: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    """Sostituisce i segnaposto dei bandi (dati pubblici); quelli personali restano."""
    primo = opportunita[0] if opportunita else None
    titolo = primo.titolo if primo else ""
    if len(opportunita) == 2:
        titolo += " e un altro bando"
    elif len(opportunita) > 2:
        titolo += f" e altri {len(opportunita) - 1} bandi"
    valori = {
        "{titolo_bando}": titolo,
        "{scadenza}": f"{primo.scadenza:%d/%m/%Y}" if primo and primo.scadenza else "",
        "{perche}": _motivo(primo) if primo else "",
        "{link_bando}": _link(primo) if primo else "",
        "{elenco_bandi}": elenco(opportunita),
        **(extra or {}),
    }
    for k, v in valori.items():
        modello = modello.replace(k, v)
    return modello


def errori_modello(oggetto: str, testo: str, evento: str) -> list[str]:
    import re

    errori = []
    for campo, valore in (("oggetto", oggetto), ("testo", testo)):
        for x in re.findall(r"\{[^{}]*\}", valore):
            if x not in SEGNAPOSTO:
                errori.append(f"Segnaposto sconosciuto nel {campo}: {x}")
    if evento == "riepilogo" and "{riepilogo}" not in testo:
        errori.append("Il riepilogo deve contenere {riepilogo} nel testo.")
    if evento != "riepilogo" and "{riepilogo}" in oggetto + testo:
        errori.append("{riepilogo} si usa solo nel riepilogo periodico.")
    if evento != "riepilogo" and "{elenco_bandi}" not in testo and "{titolo_bando}" not in testo:
        errori.append("Nel testo serve {elenco_bandi} o {titolo_bando}.")
    return errori


# --- Esecuzione ---------------------------------------------------------------------------


@dataclass
class Risultato:
    bozze: int = 0
    inviati: int = 0
    non_inviati: int = 0
    senza_consenso: list[str] = field(default_factory=list)

    def testo(self) -> str:
        def n(quanti: int, uno: str, tanti: str) -> str:
            return f"{quanti} {uno if quanti == 1 else tanti}"

        parti = []
        if self.bozze:
            parti.append(n(self.bozze, "bozza da approvare", "bozze da approvare"))
        if self.inviati:
            parti.append(n(self.inviati, "messaggio inviato", "messaggi inviati"))
        if self.non_inviati:
            parti.append(
                n(self.non_inviati, "non inviato", "non inviati")
                + " (vedi Aggiornamenti → Non inviati)"
            )
        if self.senza_consenso:
            k = len(self.senza_consenso)
            parti.append(
                n(k, "cliente senza referenti", "clienti senza referenti") + " con il consenso"
            )
        return ", ".join(parti) or "nessun messaggio"


def responsabile(db: Session, au: Automazione) -> Utente | None:
    """A nome di chi partono i messaggi: chi gestisce l'automazione, altrimenti il titolare."""
    u = db.get(Utente, au.utente_id) if au.utente_id else None
    if u is not None and u.attivo:
        return u
    return db.scalar(
        select(Utente).where(Utente.attivo, Utente.ruolo == "titolare").order_by(Utente.id)
    )


def _gia_oggi(db: Session, a: Azienda, oggi: date) -> bool:
    inizio = datetime.combine(oggi, datetime.min.time(), UTC)
    return (
        db.scalar(
            select(AggiornamentoCliente.id).where(
                AggiornamentoCliente.azienda_id == a.id,
                AggiornamentoCliente.automazione_id.is_not(None),
                AggiornamentoCliente.creato_il >= inizio,
            )
        )
        is not None
    )


def _riepilogo_dovuto(db: Session, a: Azienda, au: Automazione, oggi: date) -> bool:
    from sqlalchemy import func

    ultima = db.scalar(
        select(func.max(AggiornamentoCliente.creato_il)).where(
            AggiornamentoCliente.azienda_id == a.id,
            AggiornamentoCliente.automazione_id == au.id,
        )
    )
    giorni = FREQUENZE.get(au.frequenza or "", 30)
    return ultima is None or ultima.date() <= oggi - timedelta(days=giorni)


def messaggio(
    db: Session, a: Azienda, au: Automazione, oggi: date, anteprima: bool = False
) -> tuple[str, str, list[Opportunita]] | None:
    """(oggetto, testo con i segnaposto personali, opportunità citate) o None se non c'è
    niente da dire a questo cliente."""
    if au.evento == "riepilogo":
        giorni = FREQUENZE.get(au.frequenza or "", 30)
        dati = agg._contenuto(db, a, oggi - timedelta(days=giorni), au.punteggio_minimo)
        ammessi = set(au.ambiti) if au.ambiti else set(AMBITI)
        dati["opportunita"] = [o for o in dati["opportunita"] if _ambito(o) in ammessi]
        corpo = riepilogo(dati)
        testo = au.testo.replace("{riepilogo}", corpo)
        return compila(au.oggetto, []), compila(testo, []), list(dati["opportunita"])
    trovate = opportunita_per(db, a, au, oggi, anteprima=anteprima)
    if not trovate:
        return None
    return compila(au.oggetto, trovate), compila(au.testo, trovate), trovate


def esegui_una(
    db: Session, au: Automazione, oggi: date | None = None, base_url: str = ""
) -> Risultato:
    oggi = oggi or date.today()
    r = Risultato()
    utente = responsabile(db, au)
    for a in clienti(db, au):
        if _gia_oggi(db, a, oggi):
            continue
        if au.evento == "riepilogo" and not _riepilogo_dovuto(db, a, au, oggi):
            continue
        canale = None if au.canale == "preferito" else au.canale
        referente, canale = agg.destinatario(a, canale)
        if referente is None:
            r.senza_consenso.append(a.ragione_sociale)
            continue
        m = messaggio(db, a, au, oggi)
        if m is None:
            continue
        oggetto, testo, citate = m
        nuovo = agg._riscrivi_con_ai(db, testo, PROMPT_AI) if au.usa_ai else None
        ag = AggiornamentoCliente(
            azienda_id=a.id,
            referente_id=referente.id,
            canale=canale,
            oggetto=oggetto[:300],
            testo=nuovo or testo,
            bandi_citati=[o.bando_id for o in citate if o.bando_id] or None,
            appalti_citati=[o.appalto_id for o in citate if o.appalto_id] or None,
            stato="bozza",
            generato_con_ai=nuovo is not None,
            automazione_id=au.id,
        )
        db.add(ag)
        db.flush()
        db.refresh(ag)  # carica referente e azienda per l'invio
        if au.azione == "invio" and utente is not None:
            ag.stato, ag.approvato_da_id = "approvato", utente.id
            ag.approvato_il = datetime.now(UTC)
            esito = agg.invia(db, ag, utente, base_url)
            if esito.ok:
                r.inviati += 1
            else:
                r.non_inviati += 1
        else:
            r.bozze += 1
    au.ultima_esecuzione = datetime.now(UTC)
    au.ultimo_esito = r.testo()
    return r


def esegui(db: Session, oggi: date | None = None) -> str:
    """Tutte le automazioni attive, in ordine di creazione (passo del job del mattino)."""
    from app.config import get_settings

    base_url = get_settings().url_pubblico.rstrip("/")
    righe = []
    for au in db.scalars(select(Automazione).where(Automazione.attiva).order_by(Automazione.id)):
        try:
            righe.append(f"{au.nome}: {esegui_una(db, au, oggi, base_url).testo()}")
            db.commit()
        except Exception as e:  # una regola sbagliata non ferma le altre
            db.rollback()
            log.exception("Automazione %s non riuscita", au.id)
            righe.append(f"{au.nome}: errore {type(e).__name__}")
    return "Automazioni: " + ("; ".join(righe) if righe else "nessuna attiva") + "."
