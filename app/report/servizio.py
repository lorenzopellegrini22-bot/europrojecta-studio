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


def sezioni(a: Azienda | None) -> tuple[tuple[str, str], ...]:
    """Titoli delle sezioni: per un comune o un altro ente «al vostro ente»."""
    if getattr(a, "tipo_soggetto", None) == "ente_pubblico":
        return tuple(
            (k, "Perché è adatto al vostro ente" if k == "perche_adatto" else t) for k, t in SEZIONI
        )
    return SEZIONI


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
        sezioni=[(k, t, r.contenuto.get(k) or "") for k, t in sezioni(r.azienda)],
        consulente=consulente,
        attribuzione=ATTRIBUZIONE,
        oggi=datetime.now(UTC),
    )


def pdf(r: ReportBando, b: Bando, consulente: str) -> bytes:
    """PDF con WeasyPrint dove ci sono le sue librerie di sistema (Docker); altrimenti
    (Vercel, Windows) con PyMuPDF, che non ha bisogno di nulla fuori da Python."""
    documento = html(r, b, consulente)
    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        risultato = _pdf_pymupdf(documento, consulente)
    else:
        risultato = bytes(HTML(string=documento).write_pdf())
    r.pdf_generato_il = datetime.now(UTC)
    return risultato


MARGINE = 56.7  # 2 cm in punti tipografici


def _pdf_pymupdf(documento: str, consulente: str) -> bytes:
    try:
        import pymupdf
    except ImportError as e:
        raise ErrorePDF("La generazione dei PDF non è disponibile su questa installazione.") from e
    import io
    import re

    # MuPDF non conosce @page con le regole annidate (il piede di WeasyPrint): si toglie,
    # il piede si aggiunge sotto a mano
    documento = re.sub(r"@page\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", documento)
    buffer = io.BytesIO()
    storia = pymupdf.Story(html=documento)
    scrittore = pymupdf.DocumentWriter(buffer)
    pagina_a4 = pymupdf.paper_rect("a4")
    area = pagina_a4 + (MARGINE, MARGINE, -MARGINE, -MARGINE - 6)
    altro = True
    while altro:
        dispositivo = scrittore.begin_page(pagina_a4)
        altro, _ = storia.place(area)
        storia.draw(dispositivo)
        scrittore.end_page()
    scrittore.close()
    # Piede di pagina come nel modello WeasyPrint: «consulente · pagina N di M»
    doc = pymupdf.open("pdf", buffer.getvalue())
    for i, pagina in enumerate(doc, start=1):
        piede = f"{consulente} · pagina {i} di {doc.page_count}"
        larghezza = pymupdf.get_text_length(piede, fontname="helv", fontsize=8)
        pagina.insert_text(
            ((pagina_a4.width - larghezza) / 2, pagina_a4.height - MARGINE / 2),
            piede,
            fontname="helv",
            fontsize=8,
            color=(0.4, 0.4, 0.4),
        )
    return doc.tobytes(garbage=3, deflate=True)


def aggiorna(r: ReportBando, form: dict[str, Any]) -> None:
    r.contenuto = {k: (form.get(k) or "").strip()[:5000] for k, _ in SEZIONI}


def word(r: ReportBando, b: Bando, consulente: str) -> bytes:
    """Lo stesso report in Word (.docx), da ritoccare con Word o Google Documenti prima
    dell'invio: stesse sezioni e stesso testo del PDF."""
    import io

    from docx import Document
    from docx.shared import Pt, RGBColor

    from app.documenti.word import pulito

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.core_properties.title = pulito(b.titolo or "Report")[:250]
    doc.core_properties.author = pulito(consulente or "")[:250]
    sopra = doc.add_paragraph()
    sopra.add_run(f"Scheda bando · preparata per {pulito(r.azienda.ragione_sociale)}").italic = True
    doc.add_heading(pulito(b.titolo or "Bando"), level=1)
    dettagli = [b.ente or ""]
    if b.data_chiusura:
        dettagli.append(f"scadenza {b.data_chiusura:%d/%m/%Y}")
    dettagli.append(f"{datetime.now(UTC):%d/%m/%Y}")
    doc.add_paragraph(" · ".join(pulito(x) for x in dettagli if x))
    for chiave, titolo in sezioni(r.azienda):
        testo = pulito((r.contenuto or {}).get(chiave) or "").strip()
        if not testo:
            continue
        doc.add_heading(titolo, level=2)
        for riga in testo.splitlines():
            riga = riga.strip()
            if not riga:
                continue
            if riga.startswith("• "):
                doc.add_paragraph(riga[2:], style="List Bullet")
            else:
                doc.add_paragraph(riga)
    chiusura = doc.add_paragraph("Restiamo a disposizione per preparare insieme la domanda.")
    chiusura.add_run("\n")
    chiusura.add_run(pulito(consulente or "")).bold = True
    fonte = ATTRIBUZIONE + (f" Link ufficiale: {b.link_ufficiale}" if b.link_ufficiale else "")
    piccolo = doc.add_paragraph().add_run(pulito(fonte))
    piccolo.font.size = Pt(8)
    piccolo.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    uscita = io.BytesIO()
    doc.save(uscita)
    return uscita.getvalue()
