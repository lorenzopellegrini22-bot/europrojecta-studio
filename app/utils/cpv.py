"""Divisioni del vocabolario comune per gli appalti (CPV, Regolamento (CE) n. 213/2008):
le prime due cifre del codice. Servono come "categorie" per filtrare gli appalti."""

import re

DIVISIONI_CPV: dict[str, str] = {
    "03": "Agricoltura, allevamento, pesca, silvicoltura",
    "09": "Prodotti petroliferi, combustibili, elettricità e altre fonti di energia",
    "14": "Prodotti delle miniere e metalli di base",
    "15": "Prodotti alimentari, bevande e tabacco",
    "16": "Macchinari agricoli",
    "18": "Indumenti, calzature, articoli da viaggio e accessori",
    "19": "Pelle, tessuti, plastica e gomma",
    "22": "Stampati e prodotti affini",
    "24": "Prodotti chimici",
    "30": "Macchine per ufficio ed elaboratori elettronici",
    "31": "Macchine e apparecchi elettrici, illuminazione",
    "32": "Apparecchiature radiotelevisive e per le telecomunicazioni",
    "33": "Apparecchiature mediche, prodotti farmaceutici e per la cura personale",
    "34": "Attrezzature di trasporto",
    "35": "Apparecchiature di sicurezza, antincendio, polizia e difesa",
    "37": "Strumenti musicali, articoli sportivi, giochi e giocattoli",
    "38": "Apparecchiature di laboratorio, ottiche e di precisione",
    "39": "Mobili, arredamento, elettrodomestici e prodotti per la pulizia",
    "41": "Acqua captata e depurata",
    "42": "Macchine industriali",
    "43": "Macchinari per miniere, cave e costruzioni",
    "44": "Strutture e materiali per costruzione",
    "45": "Lavori di costruzione",
    "48": "Pacchetti software e sistemi di informazione",
    "50": "Servizi di riparazione e manutenzione",
    "51": "Servizi di installazione",
    "55": "Servizi alberghieri, di ristorazione e di vendita al dettaglio",
    "60": "Servizi di trasporto",
    "63": "Servizi di supporto ai trasporti e agenzie di viaggio",
    "64": "Servizi di poste e telecomunicazioni",
    "65": "Servizi di pubblica utilità",
    "66": "Servizi finanziari e assicurativi",
    "70": "Servizi immobiliari",
    "71": "Servizi di architettura, ingegneria e ispezione",
    "72": "Servizi informatici",
    "73": "Servizi di ricerca e sviluppo",
    "75": "Servizi di pubblica amministrazione, difesa e previdenza",
    "76": "Servizi per l'industria petrolifera e del gas",
    "77": "Servizi agricoli, forestali, orticoli e di acquacoltura",
    "79": "Servizi per le imprese: legali, marketing, consulenza, selezione, stampa, sicurezza",
    "80": "Servizi di istruzione e formazione",
    "85": "Servizi sanitari e di assistenza sociale",
    "90": "Servizi fognari, rifiuti, pulizia e ambientali",
    "92": "Servizi ricreativi, culturali e sportivi",
    "98": "Altri servizi di comunità, sociali e personali",
}


def codici_precisi(testo: str | None) -> list[str]:
    """«79710000, 909» → ["7971", "909"]: il codice CPV senza gli zeri finali è il prefisso
    di tutti i suoi sottocodici (le prime due cifre, la divisione, restano: «90000000» → «90»).
    Si tengono i codici di una divisione esistente."""
    out = []
    for pezzo in re.split(r"[\s,;]+", testo or ""):
        cifre = re.sub(r"\D", "", pezzo.split("-")[0])[:8]
        while len(cifre) > 2 and cifre.endswith("0"):
            cifre = cifre[:-1]
        if len(cifre) >= 2 and cifre[:2] in DIVISIONI_CPV and cifre not in out:
            out.append(cifre)
    return out


def interessi_effettivi(codici: list[str] | None) -> set[str]:
    """Una divisione (79) scelta insieme a un codice più preciso (7971) non conta: vince il
    codice preciso, altrimenti la divisione riporterebbe tutte le gare che si volevano evitare."""
    tutti = set(codici or [])
    return {c for c in tutti if not any(x != c and x.startswith(c) for x in tutti)}


def corrisponde(codice: str, interessi: set[str]) -> str | None:
    """L'interesse più preciso che contiene il codice CPV della gara, se c'è."""
    trovati = [i for i in interessi if codice.startswith(i)]
    return max(trovati, key=len) if trovati else None


def etichetta(codice: str) -> str:
    nome = DIVISIONI_CPV.get(codice[:2], codice[:2])
    return nome if len(codice) == 2 else f"{nome} (CPV {codice}…)"
