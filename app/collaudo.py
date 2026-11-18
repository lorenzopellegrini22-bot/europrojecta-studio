# ruff: noqa: E501
"""Collaudo prima della consegna (brief, sezione 14; preventivo): 20 bandi reali aperti e
10 aziende di prova, esito atteso etichettato a mano con il consulente.

Requisiti:
- precisione: almeno l'80% dei match proposti è pertinente;
- zero esclusioni sbagliate: nessuna coppia etichettata «compatibile» è scartata dai filtri.

Due comandi:
- `python -m app.cli collaudo-prepara --file collaudo.json`: sceglie 20 bandi aperti vari
  dal database e scrive il file con 10 aziende di prova e una riga da etichettare per coppia;
- `python -m app.cli collaudo --file collaudo.json --report collaudo.md`: valuta tutte le coppie
  con le stesse regole dell'abbinamento notturno e scrive il report in Markdown.

Le aziende di prova non vengono salvate nel database (sono oggetti in memoria).

Formato del file:
{"aziende": [{"chiave": "A1", "ragione_sociale": "...", "forma_giuridica": "S.r.l.",
  "dimensione": "piccola", "regione": "Lazio", "provincia": "VT", "comune": "Viterbo",
  "ateco": "25.62", "dipendenti": 12, "fatturato": 1500000, "data_costituzione": "2015-03-01",
  "progetti": [{"titolo": "...", "descrizione": "...", "categorie": ["..."], "importo": 200000}]}],
 "bandi": [{"fonte": "incentivi_italia", "id_esterno": "1234", "titolo": "..."}],
 "etichette": [{"azienda": "A1", "bando": "incentivi_italia:1234",
                "atteso": "compatibile" | "non_compatibile" | "dubbio" | "da_etichettare",
                "nota": "..."}]}
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Azienda, AziendaAteco, Bando, Fonte, Sede
from app.opportunita.abbinamento import PUNTEGGIO_MINIMO, Contesto, valuta_bando_con_motivo

SOGLIA_PRECISIONE = 0.8

# 10 aziende di prova realistiche (fittizie), nelle regioni del consulente
AZIENDE_PROVA: list[dict[str, Any]] = [
    {"chiave": "A01", "ragione_sociale": "Officine Tuscia S.r.l.", "forma_giuridica": "S.r.l.", "dimensione": "piccola", "regione": "Lazio", "provincia": "VT", "comune": "Viterbo", "ateco": "25.62", "dipendenti": 18, "fatturato": 2400000, "data_costituzione": "2009-04-15",
     "progetti": [{"titolo": "Nuovo centro di lavoro CNC", "descrizione": "Acquisto di un centro di lavoro a controllo numerico 5 assi e software CAM", "categorie": ["macchinari e attrezzature", "digitale e software"], "importo": 350000}]},
    {"chiave": "A02", "ragione_sociale": "Agriturismo Colle Verde", "forma_giuridica": "Società agricola semplice", "dimensione": "micro", "regione": "Umbria", "provincia": "PG", "comune": "Todi", "ateco": "01.21", "dipendenti": 4, "fatturato": 380000, "data_costituzione": "2012-06-01",
     "progetti": [{"titolo": "Cantina ed energia", "descrizione": "Ammodernamento della cantina e impianto fotovoltaico aziendale", "categorie": ["macchinari e attrezzature", "energia e ambiente"], "importo": 180000}]},
    {"chiave": "A03", "ragione_sociale": "Studio Digitale Marche S.r.l.", "forma_giuridica": "S.r.l.", "dimensione": "micro", "regione": "Marche", "provincia": "AN", "comune": "Ancona", "ateco": "62.01", "dipendenti": 7, "fatturato": 650000, "data_costituzione": "2021-09-20",
     "progetti": [{"titolo": "Piattaforma SaaS", "descrizione": "Sviluppo di una piattaforma software in cloud e assunzione di due sviluppatori", "categorie": ["digitale e software", "assunzioni", "ricerca e sviluppo"], "importo": 220000}]},
    {"chiave": "A04", "ragione_sociale": "Edil Firenze S.p.A.", "forma_giuridica": "S.p.A.", "dimensione": "media", "regione": "Toscana", "provincia": "FI", "comune": "Firenze", "ateco": "41.20", "dipendenti": 85, "fatturato": 21000000, "data_costituzione": "1998-02-10",
     "progetti": [{"titolo": "Efficienza energetica sede", "descrizione": "Riqualificazione energetica della sede e mezzi a basse emissioni", "categorie": ["energia e ambiente", "immobili e opere"], "importo": 900000}]},
    {"chiave": "A05", "ragione_sociale": "Hotel Lungomare Pesaro S.n.c.", "forma_giuridica": "S.n.c.", "dimensione": "piccola", "regione": "Marche", "provincia": "PU", "comune": "Pesaro", "ateco": "55.10", "dipendenti": 14, "fatturato": 1300000, "data_costituzione": "2004-05-05",
     "progetti": [{"titolo": "Ristrutturazione camere", "descrizione": "Ristrutturazione delle camere e digitalizzazione delle prenotazioni", "categorie": ["immobili e opere", "digitale e software"], "importo": 400000}]},
    {"chiave": "A06", "ragione_sociale": "Nuova Impresa Rossi", "forma_giuridica": "Ditta individuale", "dimensione": "micro", "regione": "Lazio", "provincia": "RM", "comune": "Roma", "ateco": "47.91", "dipendenti": 1, "fatturato": 60000, "data_costituzione": "2025-11-01",
     "progetti": [{"titolo": "Avvio e-commerce", "descrizione": "Avvio di un negozio online di prodotti tipici, sito e magazzino", "categorie": ["avvio d'impresa", "digitale e software"], "importo": 45000}]},
    {"chiave": "A07", "ragione_sociale": "Meccanica Ternana S.r.l.", "forma_giuridica": "S.r.l.", "dimensione": "piccola", "regione": "Umbria", "provincia": "TR", "comune": "Terni", "ateco": "28.41", "dipendenti": 32, "fatturato": 5200000, "data_costituzione": "2001-01-15",
     "progetti": [{"titolo": "Export Germania", "descrizione": "Partecipazione a fiere e consulenze per l'export in Germania", "categorie": ["internazionalizzazione", "consulenze"], "importo": 80000}]},
    {"chiave": "A08", "ragione_sociale": "Cooperativa Sociale Il Ponte", "forma_giuridica": "Società cooperativa", "dimensione": "piccola", "regione": "Toscana", "provincia": "PI", "comune": "Pisa", "ateco": "88.10", "dipendenti": 40, "fatturato": 1900000, "data_costituzione": "2010-03-03",
     "progetti": [{"titolo": "Formazione operatori", "descrizione": "Formazione del personale e nuovi servizi di assistenza domiciliare", "categorie": ["formazione", "assunzioni"], "importo": 120000}]},
    {"chiave": "A09", "ragione_sociale": "BioLab Siena S.r.l.", "forma_giuridica": "S.r.l.", "dimensione": "micro", "regione": "Toscana", "provincia": "SI", "comune": "Siena", "ateco": "72.11", "dipendenti": 6, "fatturato": 300000, "data_costituzione": "2023-02-14",
     "progetti": [{"titolo": "Brevetto diagnostico", "descrizione": "Ricerca industriale su un kit diagnostico e deposito del brevetto", "categorie": ["ricerca e sviluppo", "brevetti e proprietà intellettuale"], "importo": 500000}]},
    {"chiave": "A10", "ragione_sociale": "Trasporti Pontini S.r.l.", "forma_giuridica": "S.r.l.", "dimensione": "media", "regione": "Lazio", "provincia": "LT", "comune": "Latina", "ateco": "49.41", "dipendenti": 120, "fatturato": 28000000, "data_costituzione": "1995-07-01",
     "progetti": [{"titolo": "Flotta green", "descrizione": "Sostituzione di 10 mezzi pesanti con veicoli a basse emissioni", "categorie": ["macchinari e attrezzature", "energia e ambiente"], "importo": 1500000}]},
]  # fmt: skip


def azienda_di_prova(d: dict[str, Any]) -> tuple[Azienda, list[Any]]:
    """Azienda in memoria (non salvata) e i suoi progetti, per le stesse regole notturne."""
    a = Azienda(
        ragione_sociale=d["ragione_sociale"],
        forma_giuridica=d.get("forma_giuridica"),
        dimensione_manuale=d.get("dimensione"),
        dipendenti=d.get("dipendenti"),
        fatturato=Decimal(str(d["fatturato"])) if d.get("fatturato") is not None else None,
        data_costituzione=date.fromisoformat(d["data_costituzione"])
        if d.get("data_costituzione")
        else None,
        plafond_de_minimis_manuale=Decimal(str(d["plafond_de_minimis"]))
        if d.get("plafond_de_minimis") is not None
        else None,
    )
    a.sedi = [
        Sede(
            tipo="legale",
            regione=d.get("regione"),
            provincia=d.get("provincia"),
            comune_testo=d.get("comune"),
        )
    ]
    a.ateco = (
        [AziendaAteco(codice=d["ateco"], versione="2007", primario=True)] if d.get("ateco") else []
    )
    progetti = [
        SimpleNamespace(
            id=i,
            titolo=p.get("titolo", f"Progetto {i}"),
            attivo=True,
            categorie=p.get("categorie") or [],
        )
        for i, p in enumerate(d.get("progetti") or [], start=1)
    ]
    return a, progetti


def prepara(db: Session, n_bandi: int = 20, seme: int = 2026) -> dict[str, Any]:
    """Sceglie bandi aperti vari: nazionali e regionali (Lazio, Toscana, Umbria, Marche),
    in de minimis, con ATECO esclusi o ammessi, con requisiti territoriali."""
    fonti = {f.id: f.codice for f in db.scalars(select(Fonte))}
    aperti = list(
        db.scalars(
            select(Bando)
            .where(Bando.stato == "aperto", Bando.bando_canonico_id.is_(None))
            .options(selectinload(Bando.requisiti))
        )
    )
    rnd = random.Random(seme)
    gruppi: dict[str, list[Bando]] = {
        "regionali": [],
        "de minimis": [],
        "ATECO": [],
        "nazionali": [],
        "altri": [],
    }
    regioni = {"Lazio", "Toscana", "Umbria", "Marche"}
    for b in aperti:
        r = b.requisiti
        if r is None:
            gruppi["altri"].append(b)
        elif r.territorio != "nazionale" and set(r.regioni or []) & regioni:
            gruppi["regionali"].append(b)
        elif r.regime == "de_minimis":
            gruppi["de minimis"].append(b)
        elif r.ateco_ammessi or r.ateco_esclusi:
            gruppi["ATECO"].append(b)
        elif r.territorio == "nazionale":
            gruppi["nazionali"].append(b)
        else:
            gruppi["altri"].append(b)
    quote = {"regionali": 7, "de minimis": 4, "ATECO": 4, "nazionali": 4, "altri": 1}
    scelti: list[Bando] = []
    for nome, q in quote.items():
        rnd.shuffle(gruppi[nome])
        scelti += gruppi[nome][:q]
    resto = [b for b in aperti if b not in scelti]
    rnd.shuffle(resto)
    scelti += resto[: max(0, n_bandi - len(scelti))]
    bandi = [
        {"fonte": fonti[b.fonte_id], "id_esterno": b.id_esterno, "titolo": b.titolo}
        for b in scelti[:n_bandi]
    ]
    return {
        "istruzioni": (
            "Per ogni coppia metti in «atteso»: compatibile, non_compatibile o dubbio "
            "(da decidere insieme al consulente). Poi: python -m app.cli collaudo --file ..."
        ),
        "aziende": AZIENDE_PROVA,
        "bandi": bandi,
        "etichette": [
            {
                "azienda": a["chiave"],
                "bando": f"{b['fonte']}:{b['id_esterno']}",
                "atteso": "da_etichettare",
                "nota": "",
            }
            for a in AZIENDE_PROVA
            for b in bandi
        ],
    }


@dataclass
class Risultato:
    coppie: list[dict[str, Any]] = field(default_factory=list)

    def _con(self, **filtro: Any) -> list[dict[str, Any]]:
        return [c for c in self.coppie if all(c[k] == v for k, v in filtro.items())]

    @property
    def proposti_giudicati(self) -> list[dict[str, Any]]:
        return [
            c
            for c in self.coppie
            if c["proposto"] and c["atteso"] in ("compatibile", "non_compatibile")
        ]

    @property
    def precisione(self) -> float | None:
        giudicati = self.proposti_giudicati
        if not giudicati:
            return None
        return sum(c["atteso"] == "compatibile" for c in giudicati) / len(giudicati)

    @property
    def esclusioni_sbagliate(self) -> list[dict[str, Any]]:
        return [c for c in self.coppie if c["atteso"] == "compatibile" and c["escluso"]]

    @property
    def superato(self) -> bool:
        p = self.precisione
        return p is not None and p >= SOGLIA_PRECISIONE and not self.esclusioni_sbagliate


def esegui(db: Session, dati: dict[str, Any], oggi: date | None = None) -> Risultato:
    oggi = oggi or date.today()
    aziende = {d["chiave"]: (d, *azienda_di_prova(d)) for d in dati["aziende"]}
    bandi: dict[str, Bando | None] = {}
    for b in dati["bandi"]:
        chiave = f"{b['fonte']}:{b['id_esterno']}"
        bandi[chiave] = db.scalar(
            select(Bando)
            .join(Fonte)
            .where(Fonte.codice == b["fonte"], Bando.id_esterno == str(b["id_esterno"]))
            .options(selectinload(Bando.requisiti))
        )
    r = Risultato()
    for e in dati["etichette"]:
        d, a, progetti = aziende[e["azienda"]]
        b = bandi.get(e["bando"])
        if b is None:
            r.coppie.append(
                {"azienda": d["ragione_sociale"], "bando": e["bando"], "titolo": "(bando non trovato nel database)",
                 "atteso": e.get("atteso", "da_etichettare"), "proposto": False, "escluso": False,
                 "punteggio": None, "motivo": "bando non trovato", "nota": e.get("nota", "")}
            )  # fmt: skip
            continue
        esito, motivo = valuta_bando_con_motivo(a, b, Contesto(progetti=progetti, oggi=oggi))
        r.coppie.append(
            {
                "azienda": d["ragione_sociale"],
                "bando": e["bando"],
                "titolo": b.titolo,
                "atteso": e.get("atteso", "da_etichettare"),
                "escluso": esito is None,
                "proposto": esito is not None and esito.punteggio >= PUNTEGGIO_MINIMO,
                "punteggio": esito.punteggio if esito else None,
                "motivo": motivo
                or "; ".join((esito.soddisfatti + [f"? {x}" for x in esito.da_verificare])[:4]),
                "nota": e.get("nota", ""),
            }
        )
    return r


def report(r: Risultato, oggi: date | None = None) -> str:
    oggi = oggi or date.today()
    p = r.precisione
    sbagliate = r.esclusioni_sbagliate
    da_etichettare = sum(c["atteso"] in ("da_etichettare", "", None) for c in r.coppie)
    righe = [
        f"# Collaudo del matching · {oggi:%d/%m/%Y}",
        "",
        f"**Esito: {'SUPERATO' if r.superato else 'NON SUPERATO'}**",
        "",
        "| Verifica | Risultato | Richiesto |",
        "|---|---|---|",
        f"| Precisione dei match proposti | {f'{p:.0%}' if p is not None else 'n.d.'} "
        f"({sum(c['atteso'] == 'compatibile' for c in r.proposti_giudicati)} pertinenti su "
        f"{len(r.proposti_giudicati)} proposti etichettati) | almeno {SOGLIA_PRECISIONE:.0%} |",
        f"| Esclusioni sbagliate | {len(sbagliate)} | 0 |",
        "",
        f"Coppie valutate: {len(r.coppie)} · proposte: {sum(c['proposto'] for c in r.coppie)} · "
        f"escluse dai filtri: {sum(c['escluso'] for c in r.coppie)} · "
        f"senza etichetta: {da_etichettare}.",
        "",
    ]
    if sbagliate:
        righe += ["## Esclusioni sbagliate (da correggere nelle regole)", ""]
        righe += [
            f"- **{c['azienda']}** × {c['titolo']} — escluso perché: {c['motivo']}"
            for c in sbagliate
        ]
        righe.append("")
    falsi = [c for c in r.coppie if c["proposto"] and c["atteso"] == "non_compatibile"]
    if falsi:
        righe += ["## Proposte non pertinenti", ""]
        righe += [
            f"- **{c['azienda']}** × {c['titolo']} (punteggio {c['punteggio']}) — {c['motivo']}"
            for c in falsi
        ]
        righe.append("")
    mancati = [
        c
        for c in r.coppie
        if c["atteso"] == "compatibile" and not c["escluso"] and not c["proposto"]
    ]
    if mancati:
        righe += ["## Compatibili non proposte (punteggio sotto soglia, non escluse)", ""]
        righe += [
            f"- **{c['azienda']}** × {c['titolo']} (punteggio {c['punteggio']})" for c in mancati
        ]
        righe.append("")
    righe += [
        "## Tutte le coppie",
        "",
        "| Azienda | Bando | Atteso | Esito | Punteggio | Motivo |",
        "|---|---|---|---|---|---|",
    ]
    for c in r.coppie:
        stato = "escluso" if c["escluso"] else ("proposto" if c["proposto"] else "sotto soglia")
        titolo = c["titolo"][:70].replace("|", "/")
        motivo = (c["motivo"] or "")[:120].replace("|", "/")
        righe.append(
            f"| {c['azienda']} | {titolo} | {c['atteso']} | {stato} | {c['punteggio'] if c['punteggio'] is not None else '—'} | {motivo} |"
        )
    return "\n".join(righe) + "\n"


def leggi_file(percorso: str) -> dict[str, Any]:
    return json.loads(Path(percorso).read_text(encoding="utf-8"))
