"""Bando-tipo del Codice degli incentivi (d.lgs. 27 novembre 2025, n. 184, art. 6).

Fonte verificata il 18/11/2026: decreto MIMIT 18 giugno 2026 «Codice degli incentivi.
Definizione del bando-tipo» (GURI n. 164 del 17/07/2026), Allegato I
(mimit.gov.it/images/stories/normativa/Allegato_I___2.pdf). I bandi conformi sono divisi in
15 partizioni con questi titoli (l'amministrazione può aggiungerne o toglierne).

Uso nell'app:
- il prompt di estrazione indica al modello in quale partizione cercare ogni campo;
- nei PDF lunghi conformi al bando-tipo si tengono per prime le partizioni con i requisiti
  (beneficiari, operazioni, spese, agevolazioni e regime, procedura, risorse, finalità) e si
  lasciano per ultime quelle che non servono al matching (revoche, controlli, privacy…).
I bandi non conformi si leggono come prima.
"""

import re

from app.utils.ricerca import normalizza

PARTIZIONI: dict[int, str] = {
    1: "Finalità e ambito di applicazione",
    2: "Risorse disponibili",
    3: "Gestione delle attività",
    4: "Soggetti beneficiari",
    5: "Operazioni agevolabili",
    6: "Spese ammissibili",
    7: "Agevolazioni concedibili e regime applicabile",
    8: "Procedura di accesso",
    9: "Procedura di erogazione",
    10: "Variazioni",
    11: "Revoche",
    12: "Controlli",
    13: "Monitoraggio, valutazione e ulteriori obblighi del beneficiario",
    14: "Trattamento dei dati personali",
    15: "Disposizioni finali",
}
# Partizioni con i dati che servono all'estrazione, in ordine di importanza
UTILI = (4, 7, 6, 5, 8, 2, 1)
MIN_PARTIZIONI_CONFORME = 5


def _titolo_norm(titolo: str) -> str:
    return normalizza(titolo).split(",")[0].strip()


_TITOLI = {n: _titolo_norm(t) for n, t in PARTIZIONI.items()}
# «4. Soggetti beneficiari», «Art. 4 - Soggetti beneficiari», «Articolo 4 – Soggetti beneficiari»
_INTESTAZIONE = re.compile(
    r"^\s*(?:art(?:icolo|\.)?\s*)?(\d{1,2})\s*[.)\-–—:]?\s*[-–—]?\s*(.{4,90})$", re.IGNORECASE
)


def partizioni(testo: str) -> dict[int, str]:
    """Le partizioni del bando-tipo trovate nel testo (numero → testo della partizione).
    Una riga è un'intestazione se il titolo coincide (all'inizio) con uno ufficiale."""
    inizi: list[tuple[int, int]] = []  # (posizione, numero)
    posizione = 0
    for riga in testo.splitlines(keepends=True):
        m = _INTESTAZIONE.match(riga.strip())
        if m:
            titolo = normalizza(m.group(2)).strip()
            for n, ufficiale in _TITOLI.items():
                if titolo.startswith(ufficiale) and all(n != x for _, x in inizi):
                    inizi.append((posizione, n))
                    break
        posizione += len(riga)
    risultato: dict[int, str] = {}
    for i, (inizio, n) in enumerate(inizi):
        fine = inizi[i + 1][0] if i + 1 < len(inizi) else len(testo)
        risultato[n] = testo[inizio:fine].strip()
    return risultato


def conforme(testo: str) -> bool:
    return len(partizioni(testo)) >= MIN_PARTIZIONI_CONFORME


def riduci(testo: str, budget: int) -> str | None:
    """Per un testo conforme più lungo del budget: prima le partizioni utili (intere o
    troncate), poi le altre se avanza spazio. None se il testo non è conforme."""
    parti = partizioni(testo)
    if len(parti) < MIN_PARTIZIONI_CONFORME:
        return None
    ordine = [n for n in UTILI if n in parti] + [n for n in sorted(parti) if n not in UTILI]
    pezzi: list[tuple[int, str]] = []
    usati = 0
    for n in ordine:
        resto = budget - usati
        if resto < 200:
            break
        pezzo = parti[n][:resto]
        pezzi.append((n, pezzo))
        usati += len(pezzo) + 2
    # si rimettono nell'ordine originale del bando
    return "\n\n".join(p for _, p in sorted(pezzi))
