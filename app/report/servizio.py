"""Report PDF su un bando per un'azienda cliente (brief, sezione 12).

Flusso: bozza modificabile (precompilata dai dati del bando e dell'abbinamento) →
approvazione del consulente → PDF con WeasyPrint (riusa un template Jinja).

Contenuto: sintesi, perché è adatto, cosa serve (documenti e requisiti da verificare),
scadenze, citazione della fonte con la licenza.

WeasyPrint ha bisogno delle librerie Pango (presenti nell'immagine Docker); dove mancano
(es. sviluppo su Windows) la generazione del PDF dà un messaggio chiaro invece di un errore.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Azienda, Bando, Opportunita, ReportBando
from app.web import templates

SEZIONI = (
    ("sintesi", "Il bando in breve"),
    ("perche_adatto", "Perché è adatto alla vostra impresa"),
    ("cosa_serve", "Cosa serve e cosa verificare"),
    ("scadenze", "Scadenze e modalità"),
    ("note", "Note del consulente"),
)
ATTRIBUZIONE = (
    "Fonte dei dati: Incentivi Italia (incentivitalia.gov.it), licenza CC BY 4.0, "
    "dati rielaborati. Verificare sempre il testo ufficiale del bando."
)


class ErrorePDF(Exception):
    pass


def _elenco(voci: list[str]) -> str:
    return "\n".join(f"• {v}" for v in voci if v)


def contenuto_iniziale(b: Bando, o: Opportunita | None) -> dict[str, str]:
    r = b.requisiti
    sintesi = (r.sintesi if r and r.sintesi else (b.descrizione or "")[:1200]).strip()
    perche: list[str] = []
    verificare: list[str] = []
    if o is not None:
        if o.motivazione_ai:
            perche.append(o.motivazione_ai)
        perche += (o.motivi or {}).get("soddisfatti", [])
        verificare += (o.motivi or {}).get("da_verificare", [])
        verificare += (o.requisiti_ai or {}).get("da_verificare", [])
    if r is not None:
        if r.categorie_spesa:
            perche.append("Spese ammissibili: " + ", ".join(r.categorie_spesa))
        if r.forma_agevolazione:
            agevolazione = ", ".join(r.forma_agevolazione)
            if r.percentuale:
                agevolazione += f" pari al {r.percentuale:.0f}%"
            if r.importo_max:
                agevolazione += f", fino a {r.importo_max:,.0f} €".replace(",", ".")
            perche.append("Agevolazione: " + agevolazione)
        from app.extraction.schema import CAMPI

        etichette = {c.nome: c.descrizione.split(" (")[0] for c in CAMPI}
        etichette.update(
            {"date": "Date di apertura e chiusura", "importi": "Importi dell'agevolazione"}
        )
        verificare += [
            f"Da verificare sul bando: {etichette.get(c, c.replace('_', ' ')).lower()}"
            for c in (r.da_verificare or [])
        ]
    scadenze = []
    if b.data_apertura:
        scadenze.append(f"Apertura: {b.data_apertura:%d/%m/%Y}")
    if b.data_chiusura:
        scadenze.append(f"Chiusura: {b.data_chiusura:%d/%m/%Y}")
    if r is not None and r.modalita and r.modalita != "sconosciuto":
        scadenze.append(f"Modalità: {r.modalita.replace('_', ' ')}")
    if b.note_apertura_chiusura:
        scadenze.append(b.note_apertura_chiusura)
    if b.link_ufficiale:
        scadenze.append(f"Testo ufficiale: {b.link_ufficiale}")
    cosa_serve = [
        "Visura camerale aggiornata e ultimo bilancio",
        "Preventivi delle spese previste",
        "Dichiarazione de minimis (se il bando è in de minimis)",
    ] + list(dict.fromkeys(verificare))
    return {
        "sintesi": sintesi,
        "perche_adatto": _elenco(list(dict.fromkeys(perche))),
        "cosa_serve": _elenco(cosa_serve),
        "scadenze": _elenco(scadenze),
        "note": "",
    }


def apri_o_crea(db: Session, a: Azienda, b: Bando) -> ReportBando:
    r = db.scalar(
        select(ReportBando).where(ReportBando.azienda_id == a.id, ReportBando.bando_id == b.id)
    )
    if r is None:
        o = db.scalar(
            select(Opportunita).where(Opportunita.azienda_id == a.id, Opportunita.bando_id == b.id)
        )
        r = ReportBando(
            azienda_id=a.id, bando_id=b.id, contenuto=contenuto_iniziale(b, o), stato="bozza"
        )
        db.add(r)
        db.flush()
    return r


def html(r: ReportBando, b: Bando, consulente: str) -> str:
    return templates.env.get_template("report/bando_pdf.html").render(
        r=r,
        b=b,
        a=r.azienda,
        sezioni=[(k, t, r.contenuto.get(k) or "") for k, t in SEZIONI],
        consulente=consulente,
        attribuzione=ATTRIBUZIONE,
        oggi=datetime.now(UTC),
    )


def pdf(r: ReportBando, b: Bando, consulente: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        raise ErrorePDF(
            "La generazione dei PDF non è disponibile su questo computer (mancano le librerie "
            "di WeasyPrint): funziona nell'installazione Docker."
        ) from e
    risultato = HTML(string=html(r, b, consulente)).write_pdf()
    r.pdf_generato_il = datetime.now(UTC)
    return bytes(risultato)


def aggiorna(r: ReportBando, form: dict[str, Any]) -> None:
    r.contenuto = {k: (form.get(k) or "").strip()[:5000] for k, _ in SEZIONI}
