"""Scheda di un appalto: categorie SOA, estrazione di CIG e categorie dal testo, etichette in
italiano e testo riassuntivo pronto da copiare (stesso schema delle schede gara usate dai
consulenti: categorie, territorio, ente, CIG, oggetto, date, procedura, criterio, importo,
descrizione, fonte, riferimenti di legge, documentazione)."""

import re
from decimal import Decimal
from typing import Any

# Categorie di qualificazione SOA per i lavori pubblici: Allegato II.12 al d.lgs. 36/2023
# (le stesse dell'allegato A al d.P.R. 207/2010).
CATEGORIE_SOA: dict[str, str] = {
    "OG1": "Edifici civili e industriali",
    "OG2": "Restauro e manutenzione dei beni immobili sottoposti a tutela",
    "OG3": "Strade, autostrade, ponti, viadotti, ferrovie, metropolitane",
    "OG4": "Opere d'arte nel sottosuolo",
    "OG5": "Dighe",
    "OG6": "Acquedotti, gasdotti, oleodotti, opere di irrigazione e di evacuazione",
    "OG7": "Opere marittime e lavori di dragaggio",
    "OG8": "Opere fluviali, di difesa, di sistemazione idraulica e di bonifica",
    "OG9": "Impianti per la produzione di energia elettrica",
    "OG10": "Impianti per la trasformazione e distribuzione di energia elettrica e "
    "impianti di pubblica illuminazione",
    "OG11": "Impianti tecnologici",
    "OG12": "Opere ed impianti di bonifica e protezione ambientale",
    "OG13": "Opere di ingegneria naturalistica",
    "OS1": "Lavori in terra",
    "OS2-A": "Superfici decorate di beni immobili del patrimonio culturale e beni culturali mobili",
    "OS2-B": "Beni culturali mobili di interesse archivistico e librario",
    "OS3": "Impianti idrico-sanitario, cucine, lavanderie",
    "OS4": "Impianti elettromeccanici trasportatori",
    "OS5": "Impianti pneumatici e antintrusione",
    "OS6": "Finiture di opere generali in materiali lignei, plastici, metallici e vetrosi",
    "OS7": "Finiture di opere generali di natura edile e tecnica",
    "OS8": "Opere di impermeabilizzazione",
    "OS9": "Impianti per la segnaletica luminosa e la sicurezza del traffico",
    "OS10": "Segnaletica stradale non luminosa",
    "OS11": "Apparecchiature strutturali speciali",
    "OS12-A": "Barriere stradali di sicurezza",
    "OS12-B": "Barriere paramassi, fermaneve e simili",
    "OS13": "Strutture prefabbricate in cemento armato",
    "OS14": "Impianti di smaltimento e recupero rifiuti",
    "OS15": "Pulizia di acque marine, lacustri, fluviali",
    "OS16": "Impianti per centrali produzione energia elettrica",
    "OS17": "Linee telefoniche ed impianti di telefonia",
    "OS18-A": "Componenti strutturali in acciaio",
    "OS18-B": "Componenti per facciate continue",
    "OS19": "Impianti di reti di telecomunicazione e di trasmissione dati",
    "OS20-A": "Rilevamenti topografici",
    "OS20-B": "Indagini geognostiche",
    "OS21": "Opere strutturali speciali",
    "OS22": "Impianti di potabilizzazione e depurazione",
    "OS23": "Demolizione di opere",
    "OS24": "Verde e arredo urbano",
    "OS25": "Scavi archeologici",
    "OS26": "Pavimentazioni e sovrastrutture speciali",
    "OS27": "Impianti per la trazione elettrica",
    "OS28": "Impianti termici e di condizionamento",
    "OS29": "Armamento ferroviario",
    "OS30": "Impianti interni elettrici, telefonici, radiotelefonici e televisivi",
    "OS31": "Impianti per la mobilità sospesa",
    "OS32": "Strutture in legno",
    "OS33": "Coperture speciali",
    "OS34": "Sistemi antirumore per infrastrutture di mobilità",
    "OS35": "Interventi a basso impatto ambientale",
}

PROCEDURE = {
    "open": "Procedura aperta",
    "restricted": "Procedura ristretta",
    "neg-w-call": "Procedura competitiva con negoziazione",
    "neg-wo-call": "Procedura negoziata senza pubblicazione del bando",
    "comp-dial": "Dialogo competitivo",
    "innovation": "Partenariato per l'innovazione",
    "oth-single": "Altra procedura in una fase",
    "oth-mult": "Altra procedura in più fasi",
}
CRITERI = {
    "price": "Prezzo più basso",
    "cost": "Costo più basso",
    "quality": "Offerta economicamente più vantaggiosa (qualità/prezzo)",
}
BASI_GIURIDICHE = {
    "32014L0024": "Direttiva 2014/24/UE (appalti pubblici), recepita dal d.lgs. 36/2023",
    "32014L0025": "Direttiva 2014/25/UE (settori speciali), recepita dal d.lgs. 36/2023",
    "32014L0023": "Direttiva 2014/23/UE (concessioni), recepita dal d.lgs. 36/2023",
    "32009L0081": "Direttiva 2009/81/CE (difesa e sicurezza)",
}

FONTI = {
    "ted": "TED – Tenders Electronic Daily (UE)",
    "anac": "ANAC – Banca Dati Nazionale dei Contratti Pubblici (pubblicità legale)",
    "consip": "Consip – open data «Bandi e gare» (MEPA, SDAPA, convenzioni)",
}

_CIG = re.compile(r"\bCIG\b\s*(?:n\.?|nr\.?|:)?\s*[:\-]?\s*([0-9A-Z]{10})\b", re.IGNORECASE)
_SOA = re.compile(r"\b(O[GS])\s?-?\s?(\d{1,2})(?:\s?-?\s?([AB])\b)?", re.IGNORECASE)
_IMPORTO = re.compile(r"(?:€|euro)\s*([\d.]+(?:,\d{1,2})?)", re.IGNORECASE)


def estrai_cig(*testi: str | None) -> str | None:
    for t in testi:
        if t:
            m = _CIG.search(t)
            if m and any(c.isdigit() for c in m.group(1)):
                return m.group(1).upper()
    return None


def _codice_soa(prefisso: str, numero: str, lettera: str | None) -> str | None:
    base = f"{prefisso.upper()}{int(numero)}"
    if lettera and f"{base}-{lettera.upper()}" in CATEGORIE_SOA:
        return f"{base}-{lettera.upper()}"
    if base in CATEGORIE_SOA:
        return base
    return None


def estrai_soa(testo: str | None) -> list[dict[str, Any]]:
    """Categorie SOA citate nel testo, con prevalente/scorporabile e importo se scritti
    nella stessa frase (es. «Categoria Prevalente: OG6 Importo Euro 1.997.813,41»)."""
    if not testo:
        return []
    trovate: dict[str, dict[str, Any]] = {}
    codici = list(_SOA.finditer(testo))
    for i, m in enumerate(codici):
        codice = _codice_soa(m.group(1), m.group(2), m.group(3))
        if codice is None:
            continue
        # Il tipo vale per il codice più vicino: in «prevalente OG11 e per la categoria OS4»
        # OS4 non è prevalente (disciplinare del 22/11/2026). Prima del codice: fino al codice
        # precedente; dopo (tabelle «OG3 | Strade | III | prevalente»): fino al successivo
        inizio = max(m.start() - 60, codici[i - 1].end() if i else 0)
        prima = testo[inizio : m.start()].lower()
        if i and inizio == codici[i - 1].end() and "\n" in prima:
            # tabella: «prevalente» della riga sopra è del codice precedente
            prima = prima.rsplit("\n", 1)[1]
        fine = codici[i + 1].start() if i + 1 < len(codici) else len(testo)
        dopo = testo[m.end() : min(fine, m.end() + 120)]
        riga_dopo = dopo.split("\n")[0].lower()
        tipo = None
        # dopo il codice conta solo se non parla di un'altra categoria: in «OS24 in quanto
        # coperte dalla categoria prevalente OG1» (quesito su TuttoGare) la prevalente è OG1
        dopo_ok = "categori" not in riga_dopo.split("prevalente")[0]
        if "prevalent" in prima or (dopo_ok and re.search(r"(?<!non )\bprevalente\b", riga_dopo)):
            tipo = "prevalente"
        elif "scorporabil" in prima or "secondari" in prima or "scorporabil" in riga_dopo:
            tipo = "scorporabile"
        importo = None
        mi = _IMPORTO.search(dopo.split("\n")[0])
        if mi:
            from app.utils.numeri import importo_italiano

            importo = importo_italiano(mi.group(1))
        voce = trovate.setdefault(
            codice,
            {
                "codice": codice,
                "descrizione": CATEGORIE_SOA[codice],
                "tipo": None,
                "importo": None,
                "provenienza": "testo",
            },
        )
        voce["tipo"] = voce["tipo"] or tipo
        if importo is not None and voce["importo"] is None:
            voce["importo"] = str(importo)
    return list(trovate.values())


def unisci_soa(*liste: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Unisce le categorie di più fonti: i dati ANAC prevalgono su quelli letti dal testo."""
    out: dict[str, dict[str, Any]] = {}
    for lista in liste:
        for c in lista or []:
            esistente = out.get(c["codice"])
            if esistente is None or (
                esistente.get("provenienza") == "testo" and c.get("provenienza") == "anac"
            ):
                out[c["codice"]] = {**(esistente or {}), **{k: v for k, v in c.items() if v}}
            else:
                for k, v in c.items():
                    if v and not esistente.get(k):
                        esistente[k] = v
    if not out:
        return None
    # una sola prevalente: la prima trovata (le fonti si passano dalla più affidabile)
    prevalenti = [c for c in out.values() if c.get("tipo") == "prevalente"]
    for c in prevalenti[1:]:
        c["tipo"] = "scorporabile"
    return sorted(
        out.values(),
        key=lambda c: (c.get("tipo") != "prevalente", c["codice"][:2], c["codice"]),
    )


def prevalente(categorie: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not categorie:
        return None
    return next((c for c in categorie if c.get("tipo") == "prevalente"), None) or (
        categorie[0] if len(categorie) == 1 else None
    )


def _euro(v: Any) -> str:
    if v in (None, ""):
        return "—"
    d = Decimal(str(v))
    testo = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {testo}"


def _cat(c: dict[str, Any]) -> str:
    s = f"{c['codice']} - {c.get('descrizione') or CATEGORIE_SOA.get(c['codice'], '')}"
    if c.get("importo"):
        s += f" ({_euro(c['importo'])})"
    return s


def righe_scheda(a: Any) -> list[tuple[str, str]]:
    """(etichetta, valore) nell'ordine della scheda gara."""
    cats = a.categorie_soa or []
    prim = prevalente(cats)
    secondarie = [c for c in cats if c is not prim]
    ente = [a.stazione_appaltante or "—"]
    if a.ente_indirizzo:
        ente.append(a.ente_indirizzo)
    if a.ente_telefono:
        ente.append(f"tel: {a.ente_telefono}")
    if a.ente_email:
        ente.append(f"email: {a.ente_email}")
    if a.ente_sito:
        ente.append(f"sito: {a.ente_sito}")
    territorio = ", ".join(a.province or []) or ", ".join(a.regioni or []) or "Italia"
    fonte = FONTI.get(a.fonte, a.fonte)
    righe = [
        ("Categoria primaria", _cat(prim) if prim else "—"),
        ("Categorie secondarie", "; ".join(_cat(c) for c in secondarie) or "—"),
        ("Territorio", territorio.upper()),
        ("Ente", "\n".join(ente)),
        ("Codice", f"CIG {a.cig}" if a.cig else f"Avviso {a.id_esterno}"),
        ("Oggetto", a.titolo),
        ("Data inserimento", a.pubblicazione.strftime("%d-%m-%Y") if a.pubblicazione else "—"),
        ("Termina", a.scadenza.strftime("%d-%m-%Y") if a.scadenza else "—"),
        ("Tipo gara", a.tipo_procedura or "—"),
        ("Criteri di aggiudicazione", a.criterio_aggiudicazione or "—"),
        ("Luogo gara", a.luogo_esecuzione or a.citta or "—"),
        ("Importo", _euro(a.importo_stimato)),
        ("Descrizione", a.descrizione or "—"),
        ("Fonte", fonte),
        ("Riferimenti di legge", a.riferimenti_legge or "—"),
    ]
    from app.appalti.documenti import documentazione

    documenti = documentazione(a)
    if a.link_partecipazione and a.link_partecipazione not in {d["url"] for d in documenti}:
        documenti.append(
            {"titolo": "Piattaforma per presentare l'offerta", "url": a.link_partecipazione}
        )
    if a.link:
        documenti.append({"titolo": "Avviso ufficiale", "url": a.link})
    righe.append(
        (
            "Documentazione",
            "\n".join(
                (
                    f"{d['tipo']} · "
                    if d.get("tipo") and not (d.get("titolo") or "").startswith(d["tipo"])
                    else ""
                )
                + f"{d.get('titolo') or 'Documento'}: {d['url']}"
                for d in documenti
            )
            or "—",
        )
    )
    return righe


def testo_scheda(a: Any) -> str:
    return "\n".join(f"{etichetta}\t{valore}" for etichetta, valore in righe_scheda(a))


# Enti nazionali (o esteri): una loro gara senza luogo di esecuzione vale davvero per tutta Italia
ENTI_NAZIONALI = re.compile(
    r"\b(consip|ministero|ministry|anas|rete ferroviaria|ferrovie dello stato|invitalia|inps"
    r"|inail|agenzia delle (?:entrate|dogane)|agenzia del demanio|direzione centrale"
    r"|presidenza del consiglio|poste italiane|sogei|difesa|esercito|marina militare"
    r"|aeronautica|carabinieri|guardia di finanza|polizia di stato|vigili del fuoco|enav|terna"
    r"|sport e salute|european|commission|authority|agency)\b",
    re.IGNORECASE,
)


def luogo_dal_comune(territorio: Any, ente: str | None, *comuni: str | None) -> tuple:
    """(province, regioni) dal comune di esecuzione o dell'ente, per le gare senza luogo.
    TED segna «ovunque» (anyw, 00) anche gare di province, comuni e ASL: il 22/11/2026 erano
    144 su 1.618 e arrivavano ai clienti di tutta Italia. Gli enti nazionali restano senza."""
    if territorio is None or ENTI_NAZIONALI.search(ente or ""):
        return None, None
    from app.appalti.pvl import provincia_e_regione

    for nome in comuni:
        nome = re.sub(r"\s*\(.*?\)", "", nome or "").strip()  # «Ispra (VA)» → «Ispra»
        voce = territorio.comune(nome) if nome else None
        if voce is not None:
            provincia, regione = provincia_e_regione(voce.provincia)
            return ([provincia] if provincia else None), [regione or voce.regione]
        provincia, regione = provincia_e_regione(nome)  # «Reggio Calabria» (capoluogo)
        if provincia:
            return [provincia], [regione]
    return None, None
