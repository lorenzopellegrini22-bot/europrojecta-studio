"""Email del mattino (brief, sezione 11): parte ogni giorno, anche senza novità («sistema
attivo»), così il consulente sa che il sistema funziona.

Contenuto:
- nuovi match sopra soglia non ancora segnalati, raggruppati per azienda e ordinati per
  punteggio e scadenza: titolo, ente, punteggio, motivazione, scadenza, link alla scheda;
- scadenze dei prossimi 7 giorni (operazioni, presentazioni, promemoria dei clienti);
- pratiche ferme da più di GIORNI_PRATICA_FERMA giorni;
- stato delle fonti; spesa AI del mese; bozze di aggiornamento da approvare.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import AggiornamentoCliente, Fonte, Opportunita, Pratica, Utente
from app.models.pratiche import STATI_PRATICA_CONCLUSI

GIORNI_PRATICA_FERMA = 30
MAX_PER_AZIENDA = 10


def _euro(v: Any) -> str:
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def componi(
    db: Session, utente: Utente, nuove: list[Opportunita], base_url: str, oggi: date | None = None
) -> tuple[str, str]:
    from app.extraction.servizio import stato_budget
    from app.pratiche.servizio import scadenze

    oggi = oggi or date.today()
    righe = [f"Buongiorno {utente.nome},", ""]

    # 1. Nuovi match
    per_azienda: dict[int, list[Opportunita]] = {}
    for o in nuove:
        per_azienda.setdefault(o.azienda_id, []).append(o)
    if per_azienda:
        righe.append(f"NUOVE OPPORTUNITÀ ({len(nuove)} per {len(per_azienda)} clienti)")
        for lista in per_azienda.values():
            lista.sort(key=lambda o: (-o.punteggio, o.scadenza or date.max))
            a = lista[0].azienda
            righe += ["", f"■ {a.ragione_sociale}"]
            for o in lista[:MAX_PER_AZIENDA]:
                tipo = "Bando" if o.bando_id else "Appalto"
                ente = (o.bando.ente if o.bando else o.appalto.stazione_appaltante) or ""
                scad = f", scade il {o.scadenza:%d/%m/%Y}" if o.scadenza else ""
                fonte = "AI" if o.punteggio_ai is not None else "regole"
                righe.append(f"  • {tipo}: {o.titolo[:140]}")
                righe.append(f"    {ente[:80]} · punteggio {o.punteggio}/100 ({fonte}){scad}")
                motivo = o.motivazione_ai or "; ".join((o.motivi or {}).get("soddisfatti", [])[:3])
                if motivo:
                    righe.append(f"    Perché: {motivo[:300]}")
                pagina = f"/bandi/{o.bando_id}" if o.bando_id else f"/appalti/{o.appalto_id}"
                righe.append(f"    {base_url}{pagina}")
            if len(lista) > MAX_PER_AZIENDA:
                righe.append(f"  … e altre {len(lista) - MAX_PER_AZIENDA}")
            righe.append(
                f"  Segna utile / non pertinente / già noto: {base_url}/opportunita?cliente={a.id}"
            )
    else:
        righe.append("Nessun nuovo match oggi: il sistema è attivo e ha controllato i bandi.")

    # 2. Scadenze dei prossimi 7 giorni
    voci = scadenze(db, oggi, oggi + timedelta(days=7))
    righe += ["", "SCADENZE DEI PROSSIMI 7 GIORNI"]
    if voci:
        for v in voci[:30]:
            dove = (
                f"{v.pratica.azienda.ragione_sociale} (pratica {v.pratica.codice})"
                if v.pratica
                else v.promemoria.azienda.ragione_sociale
            )
            righe.append(f"  • {v.data:%d/%m}: {v.titolo} · {dove}")
    else:
        righe.append("  Nessuna.")

    # 3. Pratiche ferme
    limite = datetime.now(UTC) - timedelta(days=GIORNI_PRATICA_FERMA)
    ferme = list(
        db.scalars(
            select(Pratica)
            .where(Pratica.stato.not_in(STATI_PRATICA_CONCLUSI), Pratica.aggiornato_il < limite)
            .options(selectinload(Pratica.azienda))
            .order_by(Pratica.aggiornato_il)
            .limit(15)
        )
    )
    if ferme:
        righe += ["", f"PRATICHE FERME DA PIÙ DI {GIORNI_PRATICA_FERMA} GIORNI"]
        righe += [
            f"  • {p.codice} · {p.azienda.ragione_sociale} · ultima modifica "
            f"{p.aggiornato_il:%d/%m/%Y}"
            for p in ferme
        ]

    # 4. Stato delle fonti
    fonti = list(db.scalars(select(Fonte).where(Fonte.attiva).order_by(Fonte.nome)))
    in_errore = [f for f in fonti if f.fallimenti_consecutivi]
    righe += ["", "FONTI DEI BANDI"]
    if in_errore:
        righe += [
            f"  • ATTENZIONE: {f.nome} non risponde da {f.fallimenti_consecutivi} raccolte"
            for f in in_errore
        ]
    else:
        ultima = max((f.ultimo_successo for f in fonti if f.ultimo_successo), default=None)
        quando = f" (ultima raccolta {ultima:%d/%m %H:%M})" if ultima else ""
        righe.append(f"  Tutte le {len(fonti)} fonti funzionano{quando}.")

    # 5. Spesa AI
    b = stato_budget(db, oggi)
    if b["spesa"] or b["budget"]:
        budget = f" su {_euro(b['budget'])}" if b["budget"] else ""
        righe += ["", f"SPESA AI DEL MESE: {_euro(b['spesa'])}{budget}"]
        if b["superato"] or b["sospesa"]:
            righe.append("  ATTENZIONE: le chiamate AI sono ferme (budget esaurito o errore).")

    # 6. Bozze da approvare
    bozze = db.scalar(
        select(func.count())
        .select_from(AggiornamentoCliente)
        .where(AggiornamentoCliente.stato == "bozza")
    )
    if bozze:
        righe += [
            "",
            f"AGGIORNAMENTI AI CLIENTI DA APPROVARE: {bozze}",
            f"  {base_url}/aggiornamenti",
        ]

    righe += ["", "Buon lavoro,", "Europrojecta Studio"]
    oggetto = (
        f"{len(nuove)} nuove opportunità per {len(per_azienda)} clienti"
        if nuove
        else "Nessun nuovo match oggi · sistema attivo"
    )
    return oggetto, "\n".join(righe)
