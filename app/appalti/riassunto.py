"""Riassunto di un appalto scritto dall'AI (OpenAI, account del consulente).

All'AI arrivano solo i dati pubblici dell'appalto (la scheda), mai dati dei clienti. Il
modello è quello scelto dal consulente in Impostazioni → Integrazioni → OpenAI: nessun nome
di modello è scritto nel codice. Il riassunto viene salvato e rifatto solo su richiesta.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.appalti.scheda import testo_scheda
from app.integrazioni import servizio as integrazioni
from app.models.appalti import Appalto

ISTRUZIONI = (
    "Sei un consulente esperto di appalti pubblici italiani (d.lgs. 36/2023). Riassumi la "
    "gara qui sotto per un'impresa che valuta se partecipare. Scrivi in italiano semplice, "
    "con questi titoli:\n"
    "1. In breve (2-3 frasi: cosa si appalta, chi è l'ente, dove)\n"
    "2. Chi può partecipare (categorie SOA prevalente e scorporabili con importi, se "
    "indicate; requisiti che si deducono)\n"
    "3. Importi e scadenze (importo a base di gara, termine per le offerte, giorni rimasti)\n"
    "4. Come si partecipa (procedura, criterio di aggiudicazione, piattaforma, documenti)\n"
    "5. Punti di attenzione (cose da verificare nel disciplinare: sopralluogo, garanzie, "
    "avvalimento, subappalto, fondi PNRR/FSC…)\n"
    "Usa solo le informazioni fornite: se un dato manca scrivi «non indicato» e non "
    "inventare nulla."
)


class ErroreRiassunto(Exception):
    pass


def disponibile(db: Session) -> tuple[bool, str]:
    s = integrazioni.stato(db, "openai")
    if not s.configurata:
        return False, "Collega OpenAI in Impostazioni → Integrazioni per i riassunti."
    if not s.attiva:
        return False, "L'integrazione OpenAI è disattivata nelle impostazioni."
    if not integrazioni.leggi_valori(db, "openai").get("modello"):
        return False, "Scegli il modello da usare in Impostazioni → Integrazioni → OpenAI."
    return True, ""


def genera(db: Session, appalto: Appalto, client=None) -> str:  # type: ignore[no-untyped-def]
    ok, motivo = disponibile(db)
    if not ok:
        raise ErroreRiassunto(motivo)
    valori = integrazioni.leggi_valori(db, "openai")
    import openai

    client = client or openai.OpenAI(api_key=valori["api_key"], timeout=60.0, max_retries=1)
    oggi = datetime.now(UTC).date()
    testo = f"Data di oggi: {oggi:%d/%m/%Y}\n\n{testo_scheda(appalto)}"
    try:
        risposta = client.responses.create(
            model=valori["modello"], instructions=ISTRUZIONI, input=testo
        )
    except openai.AuthenticationError as e:
        raise ErroreRiassunto("La chiave OpenAI non è valida.") from e
    except openai.RateLimitError as e:
        raise ErroreRiassunto("OpenAI: limite di utilizzo o credito esaurito.") from e
    except openai.NotFoundError as e:
        raise ErroreRiassunto("Il modello indicato nelle impostazioni non esiste.") from e
    except openai.APIError as e:
        raise ErroreRiassunto(f"OpenAI ha risposto con un errore: {e}") from e
    riassunto = (getattr(risposta, "output_text", "") or "").strip()
    if not riassunto:
        raise ErroreRiassunto("OpenAI non ha restituito testo.")
    appalto.riassunto = riassunto
    appalto.riassunto_il = datetime.now(UTC)
    appalto.riassunto_modello = valori["modello"][:100]
    db.commit()
    return riassunto
