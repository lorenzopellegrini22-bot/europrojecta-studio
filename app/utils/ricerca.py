"""Ricerca testuale comune (bandi, appalti, clienti, pratiche).

Regole, misurate sui dati reali del 18/11/2026:
- ogni parola deve comparire in almeno uno dei campi, in qualsiasi ordine (prima una frase di
  più parole era cercata per intero: «transizione ecologica imprese» dava 0 bandi invece di 17);
- accenti ignorati («attivita» trova «attività»: prima 3 risultati invece di 411), senza
  bisogno dell'estensione unaccent: si usa translate() di PostgreSQL;
- singolare e plurale: dalle parole lunghe si toglie la vocale finale («fotovoltaico» trova
  anche «fotovoltaici»);
- parole vuote ignorate (di, e, per, …) e alcuni sinonimi del settore (startup / start up,
  PMI / piccole e medie imprese).
"""

import re
import unicodedata
from typing import Any

from sqlalchemy import and_, case, func, literal, or_

# anche gli apostrofi tipografici diventano dritti (come normalizza)
CON_ACCENTI = "àáâäãèéêëìíîïòóôöõùúûüçñ’‘"
SENZA_ACCENTI = "aaaaaeeeeiiiiooooouuuucn''"

PAROLE_VUOTE = {
    "a", "ad", "al", "alla", "alle", "agli", "ai", "con", "da", "dal", "dalla", "dei", "del",
    "della", "delle", "degli", "di", "e", "ed", "gli", "i", "il", "in", "la", "le", "lo", "nei",
    "nel", "nella", "o", "per", "su", "sul", "sulla", "tra", "un", "una", "uno",
    # parole generiche che compaiono quasi ovunque e non restringono la ricerca
    "bando", "bandi", "avviso", "avvisi", "gara", "gare",
}  # fmt: skip
# Gruppi di sinonimi: se la ricerca contiene una forma, vanno bene tutte
SINONIMI = (
    ("startup", "start up", "start-up"),
    ("pmi", "piccole e medie imprese", "micro, piccole e medie"),
    ("e-commerce", "ecommerce", "commercio elettronico"),
    ("fotovoltaic", "solare"),
    # aggiunti provando la ricerca come il consulente: «digitalizzazione» non trovava
    # «Digital Transformation» né «Bonus Export Digitale»
    ("digitalizz", "digital"),
    ("efficientamento energetico", "efficienza energetica", "risparmio energetico"),
    ("internazionalizz", "export"),
    ("assunzion", "occupazion", "nuova occupazione"),
    # appalti (prova del 22/11/2026: «ristrutturazione scuola» trovava 1 gara, «asfalto» 0)
    ("scuol", "scolast", "istituto comprensivo"),
    ("asfalt", "bitum", "pavimentazione stradale", "pavimentazioni stradali", "conglomerat"),
    ("mensa", "mense", "refezion", "ristorazione scolastica"),
    ("ristrutturazion", "riqualificazion", "manutenzione straordinaria"),
    ("verde pubblico", "manutenzione del verde", "aree verdi", "verde urbano"),
    ("pulizi", "sanificazion"),
    ("computer", "personal computer", "notebook", "postazioni di lavoro"),
)
MAX_PAROLE = 8


def normalizza(testo: str) -> str:
    """Minuscolo, senza accenti e con l'apostrofo dritto (i titoli usano spesso «’»)."""
    t = unicodedata.normalize("NFKD", testo.lower().replace("’", "'").replace("‘", "'"))
    return "".join(c for c in t if not unicodedata.combining(c))


def _radice(parola: str) -> str:
    if len(parola) > 4 and parola[-1] in "aeiou":
        return parola[:-1]
    return parola


def termini(q: str) -> list[list[str]]:
    """La ricerca divisa in gruppi: ogni gruppo deve trovarsi, basta una forma del gruppo."""
    testo = normalizza(q or "")
    # «R&S» sono due lettere sole: la ricerca restava vuota e mostrava tutti i bandi
    testo = re.sub(r"(?<!\w)r\s*&\s*s(?!\w)", " ricerca sviluppo ", testo)
    testo = testo.replace("&", " ")  # «Smart&Start» = «smart start»
    gruppi: list[list[str]] = []
    for sinonimi in SINONIMI:
        forme = [normalizza(s) for s in sinonimi]
        trovata = next((f for f in forme if re.search(rf"(?<!\w){re.escape(f)}", testo)), None)
        if trovata:
            gruppi.append(forme)
            testo = re.sub(rf"(?<!\w){re.escape(trovata)}\w*", " ", testo)
    # «d'imposta», «dell'impresa»: l'articolo apostrofato si toglie, così la parola si trova
    # sia con l'apostrofo dritto sia con quello tipografico dei titoli
    testo = re.sub(r"(?<!\w)(?:d|l|dell|dall|nell|sull|all|un|quest)'", " ", testo)
    for parola in re.findall(r"[\w.-]+", testo):
        parola = parola.strip(".-")
        if len(parola) < 2 or parola in PAROLE_VUOTE:
            continue
        gruppi.append([_radice(parola)])
    return gruppi[:MAX_PAROLE]


def _colonna(col: Any) -> Any:
    """Testo minuscolo e senza accenti. Le costanti vanno scritte nella query (non come
    parametri): solo così PostgreSQL usa gli indici a trigrammi della migrazione 0025, che
    hanno esattamente questa espressione (ricerca bandi da 0,5 s a pochi millisecondi)."""
    return func.translate(
        func.lower(func.coalesce(col, literal("", literal_execute=True))),
        literal(CON_ACCENTI, literal_execute=True),
        literal(SENZA_ACCENTI, literal_execute=True),
    )


# Colonne con indice a trigrammi (migrazione 0025): (tabella, colonna)
COLONNE_INDICIZZATE = (
    ("bando", "titolo"), ("bando", "ente"),
    ("appalto", "titolo"), ("appalto", "stazione_appaltante"),
)  # fmt: skip


def espressione_indice(colonna: str) -> str:
    """SQL dell'espressione indicizzata, identica a quella delle query (_colonna)."""
    from sqlalchemy import column
    from sqlalchemy.dialects import postgresql

    return str(
        _colonna(column(colonna)).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def oggetto_gestito_da_alembic(oggetto: Any, nome: str | None, tipo: str, *_: Any) -> bool:
    """Gli indici a trigrammi esistono solo dove c'è pg_trgm: alembic non li confronta."""
    return not (tipo == "index" and (nome or "").endswith("_trgm"))


def condizione(q: str, *colonne: Any) -> Any:
    """Condizione SQL: ogni gruppo di termini compare in almeno una delle colonne.
    Restituisce None se la ricerca è vuota."""
    gruppi = termini(q)
    if not gruppi:
        return None
    normalizzate = [_colonna(c) for c in colonne]
    clausole = []
    for forme in gruppi:
        clausole.append(
            or_(
                *(
                    col.like(literal(f"%{_escape(f)}%"), escape="\\")
                    for f in forme
                    for col in normalizzate
                )
            )
        )
    return and_(*clausole)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def separa_regione(q: str) -> tuple[str | None, str]:
    """Se la ricerca nomina una regione («digitalizzazione Toscana»), la regione diventa un
    filtro territoriale e la parola esce dalla ricerca testuale."""
    from app.utils.territorio import REGIONI

    testo = q or ""
    norm = normalizza(testo)
    for r in sorted(REGIONI, key=len, reverse=True):
        nome = normalizza(r.split("/")[0])
        m = re.search(rf"(?<!\w){re.escape(nome)}(?!\w)", norm)
        if m:
            return r, (testo[: m.start()] + " " + testo[m.end() :]).strip()
    return None, testo


def pertinenza(q: str, titolo: Any) -> Any:
    """Punteggio SQL per ordinare i risultati: 2 punti per ogni gruppo di termini trovato
    nel titolo, più 3 se il titolo contiene la frase intera. Senza ricerca: None."""
    gruppi = termini(q)
    if not gruppi:
        return None
    col = _colonna(titolo)
    punti: Any = literal(0)
    for forme in gruppi:
        trovato = or_(*(col.like(literal(f"%{_escape(f)}%"), escape="\\") for f in forme))
        punti = punti + case((trovato, 2), else_=0)
    frase = " ".join(normalizza(q).split())
    if len(gruppi) > 1 and frase:
        punti = punti + case((col.like(literal(f"%{_escape(frase)}%"), escape="\\"), 3), else_=0)
    return punti
