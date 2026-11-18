"""Territorio con la tabella ISTAT dei comuni: riconoscimento del comune della sede e
confronto affidabile di comuni e province dei bandi (codici invece di nomi scritti a mano).

Se la tabella è vuota (ISTAT non ancora caricato) tutte le funzioni restituiscono None e il
matching usa il confronto per nome di prima.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Comune, Sede
from app.riferimenti.istat import norm_nome

# Modi comuni di scrivere le province diversi dal nome ISTAT
ALIAS = {
    "reggio emilia": "reggio nell emilia",
    "reggio calabria": "reggio di calabria",
    "aosta": "valle d aosta",
    "pesaro urbino": "pesaro e urbino",
    "forli": "forli cesena",
    "massa carrara": "massa carrara",
    "monza brianza": "monza e della brianza",
    "monza": "monza e della brianza",
    "bat": "barletta andria trani",
}


@dataclass
class VoceComune:
    codice: str
    nome: str
    sigla: str
    provincia: str
    regione: str


@dataclass
class Territorio:
    per_nome: dict[str, list[VoceComune]] = field(default_factory=lambda: defaultdict(list))
    per_codice: dict[str, VoceComune] = field(default_factory=dict)
    sigla_da_provincia: dict[str, str] = field(default_factory=dict)

    def comune(self, nome: str | None, sigla: str | None = None) -> VoceComune | None:
        """Il comune con quel nome (e sigla di provincia, se il nome è ambiguo)."""
        if not nome:
            return None
        candidati = self.per_nome.get(norm_nome(nome), [])
        if sigla:
            candidati = [c for c in candidati if c.sigla == sigla.upper()]
        return candidati[0] if len(candidati) == 1 else None

    def codici(self, nome: str) -> set[str] | None:
        """Tutti i codici dei comuni con quel nome (None se il nome non esiste)."""
        voci = self.per_nome.get(norm_nome(nome))
        return {v.codice for v in voci} if voci else None

    def sigla(self, provincia: str) -> str | None:
        """Sigla di una provincia scritta per esteso («Città metropolitana di Roma Capitale»,
        «Provincia di Firenze», «Firenze») o già come sigla."""
        p = norm_nome(provincia)
        for prefisso in ("citta metropolitana di ", "provincia autonoma di ", "provincia di "):
            p = p.removeprefix(prefisso)
        p = p.removesuffix(" capitale")
        if len(p) == 2 and p.upper() in self.sigla_da_provincia.values():
            return p.upper()
        return self.sigla_da_provincia.get(p) or self.sigla_da_provincia.get(ALIAS.get(p, ""))


_CACHE: dict[str, Territorio | None] = {}


def in_memoria(db: Session) -> Territorio | None:
    """Territorio caricato una volta per processo (si azzera quando si ricaricano i dati)."""
    if "t" not in _CACHE or _CACHE["t"] is None:
        _CACHE["t"] = carica(db)
    return _CACHE["t"]


def azzera_cache() -> None:
    _CACHE.clear()


def carica(db: Session) -> Territorio | None:
    t = Territorio()
    for c in db.scalars(select(Comune)):
        v = VoceComune(c.codice_istat, c.nome, c.sigla_provincia, c.provincia, c.regione)
        t.per_nome[c.nome_norm].append(v)
        t.per_codice[c.codice_istat] = v
        nome_prov = norm_nome(c.provincia)
        t.sigla_da_provincia.setdefault(nome_prov, c.sigla_provincia)
        # nomi bilingui («Bolzano/Bozen», «Valle d'Aosta/Vallée d'Aoste»): anche la parte italiana
        t.sigla_da_provincia.setdefault(norm_nome(c.provincia.split("/")[0]), c.sigla_provincia)
    return t if t.per_codice else None


def completa_sede(t: Territorio | None, sede: Sede) -> bool:
    """Riconosce il comune della sede: codice ISTAT e, se mancano, provincia e regione.
    Restituisce True se il comune è stato riconosciuto."""
    if t is None or sede is None or not sede.comune_testo:
        return False
    c = t.comune(sede.comune_testo, sede.provincia)
    if c is None:
        sede.codice_istat = None
        return False
    sede.codice_istat = c.codice
    sede.provincia = sede.provincia or c.sigla
    sede.regione = sede.regione or c.regione
    return True
