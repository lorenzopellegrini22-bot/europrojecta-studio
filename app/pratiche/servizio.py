"""Logica delle pratiche: creazione da un modello, avanzamento delle fasi, comunicazioni,
scadenzario. Le funzioni non fanno commit: lo fa la route, insieme all'audit."""

import re
import string
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Azienda,
    Bando,
    ModelloFase,
    ModelloOperazione,
    ModelloPratica,
    Pratica,
    PraticaDato,
    PraticaDocumento,
    PraticaEvento,
    PraticaFase,
    PraticaOperazione,
    Utente,
)
from app.models.pratiche import STATI_PRATICA_CONCLUSI

ETICHETTE_STATO_PRATICA = {
    "individuato": "Individuato",
    "proposto": "Proposto al cliente",
    "in_preparazione": "In preparazione",
    "presentata": "Domanda presentata",
    "ammessa": "Ammessa",
    "respinta": "Respinta",
    "rendicontazione": "In rendicontazione",
    "chiusa": "Chiusa",
}
ETICHETTE_TIPO_OPERAZIONE = {
    "attivita": "Attività",
    "documenti": "Checklist documenti",
    "dati": "Dati da raccogliere",
    "comunicazione": "Comunicazione al cliente",
}
ETICHETTE_STATO_DOCUMENTO = {
    "mancante": "Mancante",
    "richiesto": "Richiesto",
    "ricevuto": "Ricevuto",
    "verificato": "Verificato",
}

# Segnaposto disponibili nei testi delle comunicazioni
SEGNAPOSTO = {
    "ragione_sociale": "Ragione sociale del cliente",
    "referente": "Nome del referente principale",
    "bando": "Titolo del bando",
    "codice_pratica": "Codice della pratica",
    "scadenza_presentazione": "Scadenza di presentazione della domanda",
    "documenti_mancanti": "Elenco dei documenti non ancora ricevuti",
    "responsabile": "Nome del consulente responsabile",
    "protocollo": "Numero di protocollo della domanda",
}


# --- Validazioni -------------------------------------------------------------------------


def partita_iva_valida(piva: str) -> bool:
    """Partita IVA italiana: 11 cifre con cifra di controllo (algoritmo di Luhn modificato)."""
    if not re.fullmatch(r"\d{11}", piva):
        return False
    somma = 0
    for i, c in enumerate(piva[:10]):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(piva[10])


def righe(testo: str | None) -> list[str] | None:
    """Testo di una textarea → lista di righe non vuote (None se vuota: mai liste vuote)."""
    valori = [r.strip() for r in (testo or "").splitlines() if r.strip()]
    return valori or None


# --- Creazione ---------------------------------------------------------------------------


def nuovo_codice(db: Session, oggi: date) -> str:
    """Codice progressivo per anno: 2026-0001, 2026-0002, …"""
    prefisso = f"{oggi.year}-"
    ultimo = db.scalar(select(func.max(Pratica.codice)).where(Pratica.codice.like(f"{prefisso}%")))
    n = int(ultimo.removeprefix(prefisso)) + 1 if ultimo else 1
    return f"{prefisso}{n:04d}"


def _copia_operazione(mo: ModelloOperazione, numero: int) -> PraticaOperazione:
    op = PraticaOperazione(
        numero=numero,
        titolo=mo.titolo,
        descrizione=mo.descrizione,
        tipo=mo.tipo,
        stato="da_fare",
        giorni_previsti=mo.giorni_previsti,
        oggetto_email=mo.oggetto_email,
        testo_email=mo.testo_email,
    )
    op.documenti = [PraticaDocumento(nome=n, stato="mancante") for n in mo.documenti or []]
    op.dati = [PraticaDato(etichetta=e) for e in mo.campi or []]
    return op


def crea_pratica(
    db: Session,
    utente: Utente,
    azienda: Azienda,
    oggi: date,
    bando: Bando | None = None,
    bando_testo: str | None = None,
    modello: ModelloPratica | None = None,
    responsabile: Utente | None = None,
    scadenza_presentazione: date | None = None,
) -> Pratica:
    pratica = Pratica(
        codice=nuovo_codice(db, oggi),
        azienda=azienda,
        bando=bando,
        bando_testo=None if bando else bando_testo,
        modello=modello,
        stato="in_preparazione",
        responsabile=responsabile,
        scadenza_presentazione=scadenza_presentazione or (bando.data_chiusura if bando else None),
    )
    if modello is not None:
        for mf in modello.fasi:
            fase = PraticaFase(
                numero=mf.numero, nome=mf.nome, descrizione=mf.descrizione, stato="da_iniziare"
            )
            fase.operazioni = [
                _copia_operazione(mo, i) for i, mo in enumerate(mf.operazioni, start=1)
            ]
            pratica.fasi.append(fase)
    else:
        fase = PraticaFase(numero=1, nome="Lavorazione", stato="da_iniziare")
        pratica.fasi.append(fase)
    db.add(pratica)
    avvia_fase(pratica.fasi[0], oggi)
    evento(pratica, utente, f"Pratica aperta{f' dal modello «{modello.nome}»' if modello else ''}")
    return pratica


# --- Fasi e operazioni -------------------------------------------------------------------


def evento(
    pratica: Pratica,
    utente: Utente | None,
    nota: str,
    da_stato: str | None = None,
    a_stato: str | None = None,
) -> None:
    pratica.eventi.append(
        PraticaEvento(
            utente_id=utente.id if utente else None, nota=nota, da_stato=da_stato, a_stato=a_stato
        )
    )


def avvia_fase(fase: PraticaFase, oggi: date) -> None:
    """La fase diventa quella in corso; le operazioni ricevono la scadenza prevista."""
    fase.stato = "in_corso"
    fase.iniziata_il = fase.iniziata_il or oggi
    fase.completata_il = None
    for op in fase.operazioni:
        if op.scadenza is None and op.giorni_previsti is not None:
            op.scadenza = oggi + timedelta(days=op.giorni_previsti)


def completa_fase(pratica: Pratica, fase: PraticaFase, utente: Utente, oggi: date) -> None:
    """Chiude la fase e avvia la successiva non ancora completata."""
    fase.stato = "completata"
    fase.completata_il = oggi
    evento(pratica, utente, f"Fase {fase.numero} «{fase.nome}» completata")
    _avvia_successiva(pratica, fase, utente, oggi)


def salta_fase(pratica: Pratica, fase: PraticaFase, utente: Utente, oggi: date) -> None:
    fase.stato = "saltata"
    fase.completata_il = None
    evento(pratica, utente, f"Fase {fase.numero} «{fase.nome}» saltata")
    _avvia_successiva(pratica, fase, utente, oggi)


def _avvia_successiva(pratica: Pratica, fase: PraticaFase, utente: Utente, oggi: date) -> None:
    if pratica.fase_attiva is not None:
        return
    for f in pratica.fasi:
        if f.numero > fase.numero and f.stato == "da_iniziare":
            avvia_fase(f, oggi)
            evento(pratica, utente, f"Fase {f.numero} «{f.nome}» avviata")
            return


def attiva_fase(pratica: Pratica, fase: PraticaFase, utente: Utente, oggi: date) -> None:
    """Rende attiva una fase qualsiasi (anche per tornare indietro)."""
    for f in pratica.fasi:
        if f.stato == "in_corso" and f is not fase:
            f.stato = "da_iniziare"
    avvia_fase(fase, oggi)
    evento(pratica, utente, f"Fase {fase.numero} «{fase.nome}» resa attiva")


def imposta_stato_operazione(
    pratica: Pratica, op: PraticaOperazione, stato: str, utente: Utente
) -> None:
    if op.stato == stato:
        return
    op.stato = stato
    if stato == "fatta":
        op.completata_il = datetime.now(UTC)
        op.completata_da = utente.id
        evento(pratica, utente, f"Operazione «{op.titolo}» completata")
    else:
        op.completata_il = None
        op.completata_da = None
        if stato == "non_applicabile":
            evento(pratica, utente, f"Operazione «{op.titolo}» segnata come non applicabile")


def cambia_stato(pratica: Pratica, nuovo: str, utente: Utente, nota: str | None = None) -> None:
    if nuovo == pratica.stato:
        return
    vecchio = pratica.stato
    pratica.stato = nuovo
    testo = f"Stato: {ETICHETTE_STATO_PRATICA[vecchio]} → {ETICHETTE_STATO_PRATICA[nuovo]}"
    evento(pratica, utente, f"{testo}. {nota}" if nota else testo, vecchio, nuovo)


def avanzamento(pratica: Pratica) -> tuple[int, int]:
    """(operazioni chiuse, operazioni totali): fatte o non applicabili contano come chiuse."""
    ops = [op for f in pratica.fasi for op in f.operazioni]
    return sum(op.stato != "da_fare" for op in ops), len(ops)


# --- Comunicazioni -----------------------------------------------------------------------


class _Valori(dict):
    def __missing__(self, chiave: str) -> str:
        return "{" + chiave + "}"  # segnaposto sconosciuto: resta com'è, si vede e si corregge


def valori_segnaposto(pratica: Pratica) -> dict[str, str]:
    ref = pratica.azienda.referente_principale
    mancanti = [
        d.nome
        for f in pratica.fasi
        for op in f.operazioni
        for d in op.documenti
        if d.stato in ("mancante", "richiesto")
    ]
    return {
        "ragione_sociale": pratica.azienda.ragione_sociale,
        "referente": ref.nome_completo if ref else "",
        "bando": pratica.nome_bando,
        "codice_pratica": pratica.codice,
        "scadenza_presentazione": pratica.scadenza_presentazione.strftime("%d/%m/%Y")
        if pratica.scadenza_presentazione
        else "da definire",
        "documenti_mancanti": "\n".join(f"- {n}" for n in mancanti) or "(nessuno)",
        "responsabile": pratica.responsabile.nome if pratica.responsabile else "",
        "protocollo": pratica.protocollo or "",
    }


def componi(testo: str | None, valori: dict[str, str]) -> str:
    """Sostituisce i segnaposto {nome}; le graffe isolate o sconosciute restano intatte."""
    if not testo:
        return ""
    formattatore = string.Formatter()
    try:
        return formattatore.vformat(testo, (), _Valori(valori))
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        return testo  # graffe non bilanciate: si mostra il testo così com'è


# --- Scadenzario -------------------------------------------------------------------------


@dataclass
class VoceScadenza:
    data: date
    tipo: str  # operazione | presentazione
    titolo: str
    pratica: Pratica
    fase: PraticaFase | None = None
    operazione: PraticaOperazione | None = None
    assegnata_a: Utente | None = None


def scadenze(db: Session, dal: date, al: date, utente_id: int | None = None) -> list[VoceScadenza]:
    voci: list[VoceScadenza] = []
    q = (
        select(PraticaOperazione)
        .join(PraticaFase)
        .join(Pratica)
        .where(
            PraticaOperazione.stato == "da_fare",
            PraticaOperazione.scadenza.between(dal, al),
            Pratica.stato.not_in(STATI_PRATICA_CONCLUSI),
        )
        .options(
            selectinload(PraticaOperazione.fase)
            .selectinload(PraticaFase.pratica)
            .selectinload(Pratica.azienda),
            selectinload(PraticaOperazione.fase)
            .selectinload(PraticaFase.pratica)
            .selectinload(Pratica.responsabile),
            selectinload(PraticaOperazione.assegnatario),
        )
    )
    if utente_id:
        q = q.where(
            (PraticaOperazione.assegnata_a == utente_id)
            | (PraticaOperazione.assegnata_a.is_(None) & (Pratica.responsabile_id == utente_id))
        )
    for op in db.scalars(q):
        voci.append(
            VoceScadenza(
                data=op.scadenza,  # type: ignore[arg-type]
                tipo="operazione",
                titolo=op.titolo,
                pratica=op.fase.pratica,
                fase=op.fase,
                operazione=op,
                assegnata_a=op.assegnatario or op.fase.pratica.responsabile,
            )
        )
    qp = (
        select(Pratica)
        .where(
            Pratica.scadenza_presentazione.between(dal, al),
            Pratica.stato.in_(("individuato", "proposto", "in_preparazione")),
        )
        .options(selectinload(Pratica.azienda), selectinload(Pratica.bando))
    )
    if utente_id:
        qp = qp.where(Pratica.responsabile_id == utente_id)
    for p in db.scalars(qp):
        voci.append(
            VoceScadenza(
                data=p.scadenza_presentazione,  # type: ignore[arg-type]
                tipo="presentazione",
                titolo="Presentazione della domanda",
                pratica=p,
                assegnata_a=p.responsabile,
            )
        )
    voci.sort(key=lambda v: (v.data, v.pratica.codice))
    return voci


def rinumera(elementi: list) -> None:  # type: ignore[type-arg]
    for i, e in enumerate(sorted(elementi, key=lambda x: x.numero), start=1):
        e.numero = i


def sposta(elementi: list, elemento, direzione: int) -> None:  # type: ignore[no-untyped-def,type-arg]
    """Scambia la posizione di un elemento (fase o operazione) con il vicino."""
    ordinati = sorted(elementi, key=lambda x: x.numero)
    i = ordinati.index(elemento)
    j = i + direzione
    if 0 <= j < len(ordinati):
        ordinati[i].numero, ordinati[j].numero = ordinati[j].numero, ordinati[i].numero


def duplica_modello(db: Session, modello: ModelloPratica, nome: str) -> ModelloPratica:
    copia = ModelloPratica(nome=nome, descrizione=modello.descrizione)
    for f in modello.fasi:
        nf = ModelloFase(numero=f.numero, nome=f.nome, descrizione=f.descrizione)
        for o in f.operazioni:
            nf.operazioni.append(
                ModelloOperazione(
                    numero=o.numero,
                    titolo=o.titolo,
                    descrizione=o.descrizione,
                    tipo=o.tipo,
                    giorni_previsti=o.giorni_previsti,
                    documenti=list(o.documenti) if o.documenti else None,
                    campi=list(o.campi) if o.campi else None,
                    oggetto_email=o.oggetto_email,
                    testo_email=o.testo_email,
                )
            )
        copia.fasi.append(nf)
    db.add(copia)
    return copia
