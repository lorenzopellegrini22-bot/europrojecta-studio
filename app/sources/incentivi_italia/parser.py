"""Interpretazione di un record del JSON di Incentivi Italia (funzioni pure e testate)."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.sources.base import RecordBando, RequisitiFonte
from app.utils.territorio import REGIONI, SOGLIA_NAZIONALE

DIMENSIONI = {
    "Microimpresa": "micro",
    "Piccola Impresa": "piccola",
    "Media Impresa": "media",
    "Grande Impresa": "grande",
}
_PREFISSO_ATECO = re.compile(r"ATECO\s*(2007|2025)\s*:", re.IGNORECASE)
_CODICE_ATECO = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")
_ENTE_LOCALE = re.compile(
    r"^(comune|unione dei comuni|regione|provincia|citt[aà] metropolitana|"
    r"camera di commercio|cciaa)\b",
    re.IGNORECASE,
)
# 100 miliardi di euro: nessun incentivo reale li supera; valori più alti sono refusi della fonte
IMPORTO_MASSIMO_PLAUSIBILE = Decimal("100000000000")
CAMPI_IMPORTO = {
    "importo_min": "Agevolazione_Concedibile_Min",
    "importo_max": "Agevolazione_Concedibile_Max",
    "spesa_min": "Spesa_Ammessa_Min",
    "spesa_max": "Spesa_Ammessa_Max",
    "dotazione": "Stanziamento_incentivo",
}


def _testo(valore: Any) -> str | None:
    if valore is None:
        return None
    s = str(valore).strip()
    return s or None


def _lista(valore: Any) -> list[str] | None:
    """Lista ripulita da voci vuote (la fonte usa [" "] per "nessun valore"). Mai lista vuota."""
    if valore is None:
        return None
    voci = valore if isinstance(valore, list) else [valore]
    pulite = []
    for v in voci:
        t = _testo(v)
        if t and t not in pulite:
            pulite.append(t)
    return pulite or None


def _data(valore: Any) -> date | None:
    t = _testo(valore)
    if not t:
        return None
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        return None


def _importo(valore: Any) -> Decimal | None:
    """Gli importi a 0 nella fonte significano "non indicato".
    Importi oltre IMPORTO_MASSIMO_PLAUSIBILE sono errori di compilazione: si scartano."""
    if valore is None or valore == "":
        return None
    try:
        d = Decimal(str(valore)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if d <= 0 or d > IMPORTO_MASSIMO_PLAUSIBILE:
        return None
    return d


def _importo_sospetto(valore: Any) -> bool:
    try:
        return Decimal(str(valore)) > IMPORTO_MASSIMO_PLAUSIBILE
    except (InvalidOperation, ValueError):
        return False


def interpreta_ateco(testo: str | None) -> tuple[list[str] | None, str | None, bool]:
    """Restituisce (codici, versione, da_verificare).

    - vuoto o "Tutti i settori…" → nessun vincolo (None)
    - "ATECO 2025: 58.13; 58.12" → codici con versione
    - "82.30; 47.19" → codici senza versione dichiarata (da verificare)
    - testo non riconosciuto → nessun vincolo noto, da verificare
    """
    t = _testo(testo)
    if not t or t.lower().startswith("tutti"):
        return None, None, False
    versioni = {m.group(1) for m in _PREFISSO_ATECO.finditer(t)}
    pulito = _PREFISSO_ATECO.sub(";", t)
    codici: list[str] = []
    riconosciuti = True
    for pezzo in re.split(r"[;,\s]+", pulito):
        p = pezzo.strip().rstrip(".")
        if not p:
            continue
        if _CODICE_ATECO.match(p):
            if p not in codici:
                codici.append(p)
        else:
            riconosciuti = False
    if not codici:
        return None, None, True
    versione = versioni.pop() if len(versioni) == 1 else None
    return codici, versione, versione is None or not riconosciuti


def interpreta_territorio(
    regioni_raw: Any, comuni_raw: Any
) -> tuple[str, list[str] | None, list[str] | None]:
    regioni = [r for r in (_lista(regioni_raw) or []) if r in REGIONI]
    # A volte più comuni arrivano in un'unica voce separata da ";"
    comuni = _lista([c for voce in (_lista(comuni_raw) or []) for c in voce.split(";")])
    if len(regioni) >= SOGLIA_NAZIONALE:
        return "nazionale", None, None
    if comuni:
        return "comunale", regioni or None, comuni
    if regioni:
        return "regionale", regioni, None
    return "sconosciuto", None, None


def interpreta_aree_speciali(valore: Any) -> list[str] | None:
    voci: list[str] = []
    for v in _lista(valore) or []:
        for pezzo in v.split(","):
            p = pezzo.strip()
            if p and p != "Non applicabile" and p not in voci:
                voci.append(p)
    return voci or None


def ente_locale(ente: str | None) -> bool:
    return bool(ente and _ENTE_LOCALE.match(ente.strip()))


def requisiti_da_fonte(r: dict[str, Any]) -> RequisitiFonte:
    territorio, regioni, comuni = interpreta_territorio(r.get("Regioni"), r.get("Comuni"))
    if territorio == "nazionale" and ente_locale(r.get("Soggetto_Concedente")):
        # Errore frequente della fonte: un ente locale con "tutte le regioni". Non lo
        # trattiamo come nazionale: territorio da verificare (il bando non viene scartato).
        territorio = "sconosciuto"
    da_verificare: list[str] = []
    if territorio == "sconosciuto":
        da_verificare.append("territorio")
    if comuni:
        da_verificare.append("comuni")  # nomi testuali, non ancora codici ISTAT

    aree = interpreta_aree_speciali(r.get("Ambito_territoriale"))
    if aree:
        da_verificare.append("aree_speciali")

    dimensioni = [
        DIMENSIONI[d] for d in (_lista(r.get("Dimensione_beneficiari")) or []) if d in DIMENSIONI
    ]
    if not dimensioni:
        da_verificare.append("dimensioni")

    ateco, versione, ateco_incerto = interpreta_ateco(r.get("Codici_ATECO"))
    if ateco_incerto:
        da_verificare.append("ateco")

    for campo, colonna in CAMPI_IMPORTO.items():
        if _importo_sospetto(r.get(colonna)):
            da_verificare.append(campo)

    # Questi campi la fonte non li offre: li estrarrà l'LLM (fase 3)
    da_verificare += ["regime", "modalita", "categorie_spesa"]

    return RequisitiFonte(
        territorio=territorio,
        regioni=regioni,
        comuni=comuni,
        aree_speciali=aree,
        dimensioni=sorted(set(dimensioni), key=list(DIMENSIONI.values()).index) or None,
        ateco_ammessi=ateco,
        ateco_versione=versione,
        tipologie_soggetto=_lista(r.get("Tipologia_Soggetto")),
        settori=_lista(r.get("Settore_Attivita")),
        forma_agevolazione=_lista(r.get("Forma_agevolazione")),
        finalita=_lista(r.get("Obiettivo_Finalita")),
        **{campo: _importo(r.get(colonna)) for campo, colonna in CAMPI_IMPORTO.items()},
        da_verificare=da_verificare,
    )


def interpreta_record(r: dict[str, Any]) -> RecordBando | None:
    id_esterno = _testo(r.get("ID_Incentivo"))
    titolo = _testo(r.get("Titolo"))
    if not id_esterno or not titolo:
        return None
    return RecordBando(
        id_esterno=id_esterno,
        id_rna=_testo(r.get("idBando")),
        titolo=titolo,
        ente=_testo(r.get("Soggetto_Concedente")),
        descrizione=_testo(r.get("Descrizione")),
        link_ufficiale=_testo(r.get("Link_istituzionale")),
        link_domande=_testo(r.get("Link_piattaforma_domande")),
        data_apertura=_data(r.get("Data_apertura")),
        data_chiusura=_data(r.get("Data_chiusura")),
        note_apertura_chiusura=_testo(r.get("Note_di_apertura_chiusura")),
        raw=r,
        requisiti=requisiti_da_fonte(r),
    )
