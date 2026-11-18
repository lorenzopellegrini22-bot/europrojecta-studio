"""Schema dell'estrazione dei bandi (versione 1).

Ogni campo ha la forma {valore, confidenza: alta|media|bassa, citazione, da_verificare}.
Lo schema JSON generato qui è usato in modalità "strict" dagli output strutturati di OpenAI:
tutti i campi obbligatori, nessun campo extra, valori nulli ammessi.
"""

from dataclasses import dataclass
from typing import Any

from app.models.bandi import MODALITA, REGIMI, TERRITORI
from app.utils.territorio import REGIONI

VERSIONE_SCHEMA = "1"

CATEGORIE_SPESA = (
    "macchinari e attrezzature",
    "digitale e software",
    "ricerca e sviluppo",
    "energia e ambiente",
    "formazione",
    "assunzioni",
    "internazionalizzazione",
    "consulenze",
    "immobili e opere",
    "avvio d'impresa",
    "brevetti e proprietà intellettuale",
    "altro",
)
FORME_AGEVOLAZIONE = (
    "Contributo/Fondo perduto",
    "Prestito/Anticipo rimborsabile",
    "Agevolazione fiscale",
    "Capitale di rischio",
    "Interventi a garanzia",
    "Riduzione dei contributi di previdenza sociale",
)
DIMENSIONI = ("micro", "piccola", "media", "grande")


@dataclass(frozen=True)
class Campo:
    nome: str
    tipo: str  # testo, lista, numero, intero, data, scelta
    descrizione: str
    valori: tuple[str, ...] = ()  # vocabolario chiuso (scelta o lista)
    colonna: str | None = None  # colonna di bando_requisiti (None = campo del bando)


CAMPI: tuple[Campo, ...] = (
    Campo("ente", "testo", "Ente che eroga l'agevolazione", colonna=None),
    Campo(
        "territorio",
        "scelta",
        "Ambito territoriale: nazionale, regionale, provinciale o comunale",
        tuple(t for t in TERRITORI if t != "sconosciuto"),
        "territorio",
    ),
    Campo("regioni", "lista", "Regioni ammesse (se non nazionale)", REGIONI, "regioni"),
    Campo("province", "lista", "Province ammesse (nome per esteso)", colonna="province"),
    Campo("comuni", "lista", "Comuni ammessi", colonna="comuni"),
    Campo("dimensioni", "lista", "Dimensioni d'impresa ammesse", DIMENSIONI, "dimensioni"),
    Campo(
        "ateco_ammessi",
        "lista",
        "Codici ATECO ammessi, solo cifre e punti (es. 62.01 o 10)",
        colonna="ateco_ammessi",
    ),
    Campo("ateco_esclusi", "lista", "Codici ATECO esclusi", colonna="ateco_esclusi"),
    Campo(
        "forme_giuridiche",
        "lista",
        "Forme giuridiche ammesse (es. società di capitali, ditta individuale)",
        colonna="forme_giuridiche",
    ),
    Campo(
        "anzianita_min_mesi",
        "intero",
        "Anzianità minima dell'impresa in mesi",
        colonna="anzianita_min_mesi",
    ),
    Campo(
        "anzianita_max_mesi",
        "intero",
        "Anzianità massima dell'impresa in mesi (es. startup costituite da meno di 36 mesi)",
        colonna="anzianita_max_mesi",
    ),
    Campo(
        "categorie_spesa",
        "lista",
        "Spese ammissibili, solo nelle categorie indicate",
        CATEGORIE_SPESA,
        "categorie_spesa",
    ),
    Campo(
        "forma_agevolazione",
        "lista",
        "Forma dell'agevolazione",
        FORME_AGEVOLAZIONE,
        "forma_agevolazione",
    ),
    Campo("percentuale", "numero", "Intensità dell'aiuto in percentuale", colonna="percentuale"),
    Campo("importo_min", "numero", "Agevolazione minima in euro", colonna="importo_min"),
    Campo(
        "importo_max", "numero", "Agevolazione massima per impresa in euro", colonna="importo_max"
    ),
    Campo("dotazione", "numero", "Dotazione finanziaria complessiva in euro", colonna="dotazione"),
    Campo(
        "regime",
        "scelta",
        "Regime di aiuto",
        tuple(r for r in REGIMI if r != "sconosciuto"),
        "regime",
    ),
    Campo(
        "modalita",
        "scelta",
        "Modalità di selezione delle domande",
        tuple(m for m in MODALITA if m != "sconosciuto"),
        "modalita",
    ),
    Campo("data_apertura", "data", "Data di apertura delle domande (AAAA-MM-GG)"),
    Campo("data_chiusura", "data", "Data di chiusura delle domande (AAAA-MM-GG)"),
    Campo(
        "altri_requisiti",
        "testo",
        "Altri requisiti importanti per partecipare, in breve",
    ),
    Campo(
        "sintesi",
        "testo",
        "Sintesi in linguaggio semplice per un imprenditore, 3-5 righe",
        colonna="sintesi",
    ),
)
PER_NOME = {c.nome: c for c in CAMPI}


def _schema_valore(c: Campo) -> dict[str, Any]:
    if c.tipo == "lista":
        voce: dict[str, Any] = {"type": "string"}
        if c.valori:
            voce["enum"] = list(c.valori)
        return {"type": ["array", "null"], "items": voce}
    if c.tipo == "scelta":
        return {"type": ["string", "null"], "enum": [*c.valori, None]}
    if c.tipo == "numero":
        return {"type": ["number", "null"]}
    if c.tipo == "intero":
        return {"type": ["integer", "null"]}
    return {"type": ["string", "null"]}


def schema_json() -> dict[str, Any]:
    """JSON Schema per gli output strutturati (strict)."""
    proprieta = {}
    for c in CAMPI:
        proprieta[c.nome] = {
            "type": "object",
            "description": c.descrizione,
            "properties": {
                "valore": _schema_valore(c),
                "confidenza": {"type": "string", "enum": ["alta", "media", "bassa"]},
                "citazione": {"type": ["string", "null"]},
                "da_verificare": {"type": "boolean"},
            },
            "required": ["valore", "confidenza", "citazione", "da_verificare"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": proprieta,
        "required": [c.nome for c in CAMPI],
        "additionalProperties": False,
    }
