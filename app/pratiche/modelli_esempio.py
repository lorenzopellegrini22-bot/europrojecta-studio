"""Modelli di pratica di esempio, da caricare con un clic (o `python -m app.cli modelli-esempio`)
e poi adattare. Sono percorsi generici di finanza agevolata, non legati a un bando specifico."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelloFase, ModelloOperazione, ModelloPratica

EMAIL_DOCUMENTI = """Gentile {referente},

per preparare la domanda per «{bando}» (pratica {codice_pratica}) ci servono i seguenti documenti:

{documenti_mancanti}

La scadenza per la presentazione è il {scadenza_presentazione}: le chiediamo di inviarceli \
appena possibile.

Cordiali saluti,
{responsabile}"""

EMAIL_PRESENTATA = """Gentile {referente},

le confermiamo che la domanda di {ragione_sociale} per «{bando}» è stata presentata \
(protocollo {protocollo}).

La terremo aggiornata sull'esito.

Cordiali saluti,
{responsabile}"""

EMAIL_ESITO = """Gentile {referente},

le scriviamo per aggiornarla sull'esito della domanda per «{bando}» (pratica {codice_pratica}).

[Inserire l'esito e i prossimi passi]

Cordiali saluti,
{responsabile}"""

EMAIL_RENDICONTAZIONE = """Gentile {referente},

per la rendicontazione del progetto finanziato da «{bando}» ci servono:

{documenti_mancanti}

Cordiali saluti,
{responsabile}"""


def _op(titolo: str, tipo: str = "attivita", **kw: Any) -> dict[str, Any]:
    return {"titolo": titolo, "tipo": tipo, **kw}


MODELLI: list[dict[str, Any]] = [
    {
        "nome": "Contributo a fondo perduto - percorso completo",
        "descrizione": "Dalla verifica dei requisiti alla rendicontazione finale. "
        "Adatto a bandi regionali e nazionali con graduatoria o sportello.",
        "fasi": [
            (
                "Verifica requisiti e prefattibilità",
                "Si controlla che il cliente e il progetto rispettino i requisiti del bando.",
                [
                    _op(
                        "Dati per la verifica",
                        "dati",
                        campi=[
                            "Codice ATECO prevalente",
                            "Dimensione d'impresa",
                            "Sede operativa interessata",
                            "Importo del progetto",
                            "Aiuti de minimis ricevuti negli ultimi 3 anni",
                        ],
                        giorni_previsti=3,
                    ),
                    _op(
                        "Verifica dei requisiti di ammissibilità",
                        descrizione="ATECO, territorio, dimensione, regime di aiuto, cumulo.",
                        giorni_previsti=5,
                    ),
                    _op("Stima del contributo ottenibile", giorni_previsti=5),
                ],
            ),
            (
                "Proposta e incarico",
                "Il cliente riceve la proposta e conferisce l'incarico.",
                [
                    _op("Invio della proposta economica", giorni_previsti=2),
                    _op(
                        "Incarico firmato",
                        "documenti",
                        documenti=["Lettera d'incarico firmata", "Informativa privacy firmata"],
                        giorni_previsti=7,
                    ),
                ],
            ),
            (
                "Accesso alla piattaforma",
                "Si verifica che il cliente abbia gli strumenti digitali per la domanda.",
                [
                    _op(
                        "Strumenti digitali del legale rappresentante",
                        "documenti",
                        documenti=["SPID o CNS attiva", "Firma digitale valida", "PEC attiva"],
                        giorni_previsti=3,
                    ),
                    _op("Registrazione sulla piattaforma del bando", giorni_previsti=5),
                ],
            ),
            (
                "Raccolta documenti",
                "Si raccolgono i documenti del cliente e del progetto.",
                [
                    _op(
                        "Documenti del cliente e del progetto",
                        "documenti",
                        documenti=[
                            "Visura camerale aggiornata",
                            "Documento d'identità del legale rappresentante",
                            "Ultimi due bilanci o dichiarazioni dei redditi",
                            "Preventivi di spesa",
                            "Descrizione del progetto",
                        ],
                        giorni_previsti=10,
                    ),
                    _op(
                        "Richiesta documenti al cliente",
                        "comunicazione",
                        oggetto_email="Documenti per la domanda {bando}",
                        testo_email=EMAIL_DOCUMENTI,
                        giorni_previsti=1,
                    ),
                ],
            ),
            (
                "Predisposizione e invio della domanda",
                "Si compila, si fa firmare e si invia la domanda.",
                [
                    _op("Compilazione della domanda e degli allegati", giorni_previsti=7),
                    _op("Firma digitale del legale rappresentante", giorni_previsti=2),
                    _op(
                        "Invio della domanda",
                        "dati",
                        campi=["Data di invio", "Numero di protocollo"],
                        giorni_previsti=1,
                    ),
                    _op(
                        "Conferma di invio al cliente",
                        "comunicazione",
                        oggetto_email="Domanda presentata: {bando}",
                        testo_email=EMAIL_PRESENTATA,
                    ),
                ],
            ),
            (
                "Integrazioni",
                "Si gestiscono le eventuali richieste di integrazione dell'ente.",
                [
                    _op("Risposta alle richieste di integrazione", giorni_previsti=10),
                ],
            ),
            (
                "Esito",
                "Si registra l'esito e lo si comunica al cliente.",
                [
                    _op(
                        "Esito della domanda",
                        "dati",
                        campi=["Data dell'esito", "Esito", "Contributo concesso"],
                    ),
                    _op(
                        "Comunicazione dell'esito",
                        "comunicazione",
                        oggetto_email="Esito della domanda {bando}",
                        testo_email=EMAIL_ESITO,
                    ),
                    _op("Accettazione del contributo (se richiesta)", giorni_previsti=10),
                ],
            ),
            (
                "Variazioni di progetto",
                "Eventuali richieste di variazione o proroga.",
                [_op("Richiesta di variazione o proroga")],
            ),
            (
                "Rendicontazione ed erogazione",
                "Si raccolgono i giustificativi e si chiede l'erogazione del contributo.",
                [
                    _op(
                        "Giustificativi di spesa",
                        "documenti",
                        documenti=[
                            "Fatture quietanzate",
                            "Estratti conto con i pagamenti tracciabili",
                            "Relazione finale del progetto",
                        ],
                        giorni_previsti=30,
                    ),
                    _op(
                        "Richiesta giustificativi al cliente",
                        "comunicazione",
                        oggetto_email="Rendicontazione {bando}",
                        testo_email=EMAIL_RENDICONTAZIONE,
                    ),
                    _op("Invio della rendicontazione", giorni_previsti=15),
                    _op(
                        "Erogazione",
                        "dati",
                        campi=["Data di erogazione", "Importo erogato"],
                    ),
                ],
            ),
        ],
    },
    {
        "nome": "Voucher a sportello - percorso rapido",
        "descrizione": "Per voucher e contributi a sportello con pochi documenti e tempi stretti.",
        "fasi": [
            (
                "Verifica requisiti",
                None,
                [_op("Verifica dei requisiti e della dotazione residua", giorni_previsti=2)],
            ),
            (
                "Documenti",
                None,
                [
                    _op(
                        "Documenti per la domanda",
                        "documenti",
                        documenti=[
                            "Visura camerale",
                            "Documento d'identità del legale rappresentante",
                            "Preventivo del fornitore",
                        ],
                        giorni_previsti=5,
                    ),
                    _op(
                        "Richiesta documenti al cliente",
                        "comunicazione",
                        oggetto_email="Documenti per il voucher {bando}",
                        testo_email=EMAIL_DOCUMENTI,
                    ),
                ],
            ),
            (
                "Invio allo sportello",
                "Data e ora di apertura dello sportello da segnare in agenda.",
                [
                    _op(
                        "Invio della domanda",
                        "dati",
                        campi=["Data e ora di invio", "Numero di protocollo"],
                    )
                ],
            ),
            (
                "Esito",
                None,
                [
                    _op("Esito", "dati", campi=["Esito", "Importo concesso"]),
                    _op(
                        "Comunicazione dell'esito",
                        "comunicazione",
                        oggetto_email="Esito del voucher {bando}",
                        testo_email=EMAIL_ESITO,
                    ),
                ],
            ),
            (
                "Rendicontazione",
                None,
                [
                    _op(
                        "Giustificativi",
                        "documenti",
                        documenti=["Fattura quietanzata", "Prova del pagamento"],
                        giorni_previsti=30,
                    ),
                    _op("Invio della rendicontazione", giorni_previsti=10),
                ],
            ),
        ],
    },
    {
        "nome": "Finanziamento agevolato o garanzia",
        "descrizione": "Per finanziamenti a tasso agevolato e garanzie, con istruttoria bancaria.",
        "fasi": [
            (
                "Analisi del fabbisogno",
                None,
                [
                    _op(
                        "Dati finanziari",
                        "dati",
                        campi=[
                            "Importo del finanziamento",
                            "Durata",
                            "Finalità",
                            "Banca o intermediario",
                        ],
                    )
                ],
            ),
            (
                "Documenti per l'istruttoria",
                None,
                [
                    _op(
                        "Documenti economico-finanziari",
                        "documenti",
                        documenti=[
                            "Ultimi due bilanci",
                            "Situazione contabile aggiornata",
                            "Centrale Rischi",
                            "Piano degli investimenti",
                        ],
                        giorni_previsti=10,
                    ),
                    _op(
                        "Richiesta documenti al cliente",
                        "comunicazione",
                        oggetto_email="Documenti per {bando}",
                        testo_email=EMAIL_DOCUMENTI,
                    ),
                ],
            ),
            (
                "Domanda e istruttoria",
                None,
                [
                    _op("Presentazione della domanda", giorni_previsti=5),
                    _op("Risposte all'istruttoria della banca o del gestore", giorni_previsti=15),
                ],
            ),
            (
                "Delibera ed erogazione",
                None,
                [
                    _op(
                        "Delibera",
                        "dati",
                        campi=["Data della delibera", "Importo deliberato", "Tasso"],
                    ),
                    _op(
                        "Comunicazione al cliente",
                        "comunicazione",
                        oggetto_email="Esito della richiesta {bando}",
                        testo_email=EMAIL_ESITO,
                    ),
                ],
            ),
        ],
    },
]


def crea_modelli_esempio(db: Session) -> int:
    """Crea i modelli di esempio che non esistono già (per nome). Restituisce quanti ne crea."""
    esistenti = set(db.scalars(select(ModelloPratica.nome)))
    creati = 0
    for m in MODELLI:
        if m["nome"] in esistenti:
            continue
        modello = ModelloPratica(nome=m["nome"], descrizione=m["descrizione"])
        for nf, (nome, descrizione, operazioni) in enumerate(m["fasi"], start=1):
            fase = ModelloFase(numero=nf, nome=nome, descrizione=descrizione)
            for no, op in enumerate(operazioni, start=1):
                fase.operazioni.append(ModelloOperazione(numero=no, **op))
            modello.fasi.append(fase)
        db.add(modello)
        creati += 1
    return creati
