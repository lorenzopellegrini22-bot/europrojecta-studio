"""Abbinamento automatico clienti ↔ bandi e appalti aperti (regole, senza AI).

BANDI: si escludono i bandi incompatibili per
- tipo di soggetto (bando solo per enti o cittadini),
- territorio (bando regionale di un'altra regione),
- dimensione e ATECO, quando sia il bando sia il cliente li indicano.
Ciò che non è noto non esclude: finisce tra le cose "da verificare".

APPALTI: territorio (regione della sede + regioni di interesse; le gare nazionali vanno
sempre bene) e almeno una tra:
- categoria SOA prevalente posseduta, con classifica sufficiente (importi dell'Allegato II.12
  al d.lgs. 36/2023, con l'incremento di un quinto);
- divisione CPV tra quelle di interesse del cliente.
Un cliente senza SOA né CPV di interesse non riceve appalti (va completato il profilo).

Il punteggio (0-100) ordina le opportunità; per ogni cliente si tengono le migliori.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import AiutoDeMinimis, Azienda, Bando, Progetto
from app.models.appalti import Appalto
from app.models.opportunita import Opportunita

MAX_PER_TIPO = 30
PUNTEGGIO_MINIMO = 45

# Classifiche SOA: importo massimo dei lavori (Allegato II.12 al d.lgs. 36/2023)
CLASSIFICHE_SOA: dict[str, Decimal | None] = {
    "I": Decimal("258000"),
    "II": Decimal("516000"),
    "III": Decimal("1033000"),
    "III-bis": Decimal("1500000"),
    "IV": Decimal("2582000"),
    "IV-bis": Decimal("3500000"),
    "V": Decimal("5165000"),
    "VI": Decimal("10329000"),
    "VII": Decimal("15494000"),
    "VIII": None,  # oltre 15.494.000: senza limite
}
INCREMENTO_QUINTO = Decimal("1.2")
SOGGETTI_IMPRESA = ("impresa", "professionista", "rete", "consorzio", "cooperative")

# Bandi chiaramente di settore (parola nel titolo) → divisioni ATECO a cui sono rivolti.
# Un cliente con ATECO di un altro settore non li riceve; senza ATECO restano "da verificare".
SETTORI_BANDO: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("agricol", "agroaliment", "zootecn", "allevament", "vitivinicol", "olivicol", "psr ", "csr "),
     ("01", "02", "03", "10", "11"), "agricoltura e agroalimentare"),
    (("pesca", "acquacoltura", "feampa"), ("03", "10"), "pesca e acquacoltura"),
    (("cinema", "audiovisiv", "spettacolo", "teatr", "musica", "cultura", "editoria",
      "immaginario"), ("58", "59", "60", "90", "91", "93"), "cultura e spettacolo"),
    (("turism", "ricettiv", "albergh", "alberghi", "travel market", "tour operator"),
     ("55", "56", "79"), "turismo"),
    (("sport",), ("93",), "sport"),
)  # fmt: skip


def _sezione_settore(titolo: str) -> tuple[tuple[str, ...], str] | None:
    from app.utils.ricerca import normalizza

    t = f" {normalizza(titolo)} "
    for parole, divisioni, nome in SETTORI_BANDO:
        # solo a inizio parola: «sport» non deve trovare «esportatrici» o «trasporti»
        if any(re.search(rf"(?<![a-z]){re.escape(p)}", t) for p in parole):
            return divisioni, nome
    return None


@dataclass
class Esito:
    punteggio: int
    soddisfatti: list[str] = field(default_factory=list)
    da_verificare: list[str] = field(default_factory=list)
    progetto_id: int | None = None

    def motivi(self) -> dict[str, list[str]]:
        return {"soddisfatti": self.soddisfatti, "da_verificare": self.da_verificare}


def sedi_cliente(a: Azienda) -> list[Any]:
    """Tutte le sedi del cliente: prima la legale, poi le operative. Un bando regionale o
    provinciale vale di solito per chi ha una sede (anche solo operativa) in quel territorio."""
    sedi = getattr(a, "sedi", None)
    if sedi is None:  # profili semplici (collaudo, prove): solo la sede legale
        s = getattr(a, "sede_legale", None)
        if s is None:
            return []
        campi = ("comune_testo", "provincia", "regione", "codice_istat")
        return [SimpleNamespace(tipo="legale", **{c: getattr(s, c, None) for c in campi})]
    return sorted(sedi, key=lambda s: s.tipo != "legale")


def regioni_sedi(a: Azienda) -> set[str]:
    return {s.regione for s in sedi_cliente(a) if s.regione}


def province_sedi(a: Azienda) -> set[str]:
    return {s.provincia.upper() for s in sedi_cliente(a) if s.provincia}


def _della(sede: Any) -> str:
    return "della sede" if sede.tipo == "legale" else "della sede operativa"


def regioni_cliente(a: Azienda) -> set[str]:
    """Regioni per gli appalti: quelle delle sedi più le regioni di interesse."""
    return set(a.regioni_interesse or []) | regioni_sedi(a)


def _ateco_ok(codice: str, ammessi: list[str]) -> bool:
    c = codice.replace(".", "")
    return any(c.startswith(x.replace(".", "")) for x in ammessi)


@dataclass
class Contesto:
    """Dati del cliente che servono ai filtri, caricati una volta per cliente."""

    aiuti: list[Any] = field(default_factory=list)
    progetti: list[Any] = field(default_factory=list)
    oggi: date | None = None
    territorio: Any = None  # app.riferimenti.territorio.Territorio (tabella ISTAT), se caricata
    raccordo: Any = None  # app.riferimenti.ateco.Raccordo (ATECO 2007/2025), se caricato


class Escluso(Exception):
    """Il bando è incompatibile: il motivo serve al collaudo (nessuna esclusione sbagliata)."""


# Forme giuridiche: parole chiave → famiglia. Si esclude solo se entrambe le parti sono
# riconosciute e nessuna forma del bando è della famiglia dell'azienda.
FAMIGLIE_FORMA = (
    (("srl", "s.r.l", "responsabilita limitata"), "società di capitali"),
    (("spa", "s.p.a", "per azioni", "sapa", "s.a.p.a"), "società di capitali"),
    (("societa di capitali",), "società di capitali"),
    (("snc", "s.n.c", "nome collettivo", "sas", "s.a.s", "accomandita semplice"),
     "società di persone"),
    (("societa di persone", "societa semplice", "s.s."), "società di persone"),
    (("ditta individuale", "impresa individuale", "imprenditore individuale"),
     "impresa individuale"),
    (("cooperativ", "soc. coop", "scarl", "s.c.a.r.l"), "cooperativa"),
    (("consorzi",), "consorzio"),
    (("associazion", "fondazion", "ente del terzo settore", " ets ", "onlus"), "non profit"),
    (("libero professionista", "liberi professionisti", "professionist", "lavoratore autonomo"),
     "professionista"),
)  # fmt: skip


def famiglia_forma(testo: str | None) -> str | None:
    if not testo:
        return None
    from app.utils.ricerca import normalizza

    t = f" {normalizza(testo)} "
    for parole, famiglia in FAMIGLIE_FORMA:
        if any(p in t for p in parole):
            return famiglia
    return None


def valuta_bando(a: Azienda, b: Bando, contesto: Contesto | None = None) -> Esito | None:
    """Esito dell'abbinamento, o None se il bando è incompatibile."""
    esito, _ = valuta_bando_con_motivo(a, b, contesto)
    return esito


def valuta_bando_con_motivo(
    a: Azienda, b: Bando, contesto: Contesto | None = None
) -> tuple[Esito | None, str | None]:
    """(esito, None) se il bando passa i filtri, (None, motivo) se viene escluso.
    Principio: si esclude solo quando il requisito del bando è certo e il dato del cliente
    è noto; in tutti gli altri casi il requisito è «da verificare»."""
    try:
        return _valuta(a, b, contesto or Contesto()), None
    except Escluso as e:
        return None, str(e)


def _valuta(a: Azienda, b: Bando, ctx: Contesto) -> Esito:
    req = b.requisiti
    e = Esito(punteggio=50)
    if req is None:
        e.da_verificare.append("Requisiti del bando non ancora disponibili")
        e.punteggio -= 10
        return e
    if req.tipologie_soggetto and not any(
        any(s in t.lower() for s in SOGGETTI_IMPRESA) for t in req.tipologie_soggetto
    ):
        raise Escluso("Bando non rivolto alle imprese: " + ", ".join(req.tipologie_soggetto[:3]))
    _tipologie(a, b, req, e)
    # Bandi di settore (cultura, agricoltura, turismo…): solo per chi è di quel settore
    settore = _sezione_settore(b.titolo)
    if settore:
        divisioni, nome = settore
        primario = a.ateco_primario
        if primario is not None:
            if primario.codice.replace(".", "")[:2] not in divisioni:
                raise Escluso(f"Bando per il settore {nome}, ATECO {primario.codice} diverso")
            e.soddisfatti.append(f"Bando per il settore {nome}, coerente con l'ATECO")
            e.punteggio += 10
        else:
            e.da_verificare.append(f"Bando per il settore {nome}")
            e.punteggio -= 10
    # Territorio: conta ogni sede, legale o operativa. Si esclude solo se tutte le sedi sono
    # note e nessuna è nel territorio del bando.
    sedi = sedi_cliente(a)
    con_comune = [s for s in sedi if s.comune_testo]
    if req.territorio == "comunale" and req.comuni and con_comune:
        ok = next((s for s in con_comune if _comune_ammesso(s, req.comuni, ctx)), None)
        if ok is not None:
            e.soddisfatti.append(f"Bando per il comune {_della(ok)} ({ok.comune_testo})")
            e.punteggio += 20
            return _altri_filtri(a, req, b, e, ctx)
        if len(con_comune) == len(sedi):
            raise Escluso(f"Bando per altri comuni ({', '.join(req.comuni[:3])})")
    # Bando provinciale: con la tabella ISTAT le province si confrontano per sigla
    province = getattr(req, "province", None)
    con_sigla = [s for s in sedi if s.provincia]
    if req.territorio == "provinciale" and province and con_sigla and ctx.territorio:
        sigle = {ctx.territorio.sigla(p) for p in province}
        if None not in sigle:
            ok = next((s for s in con_sigla if s.provincia.upper() in sigle), None)
            if ok is not None:
                e.soddisfatti.append(
                    f"Bando per la provincia {_della(ok)} ({ok.provincia.upper()})"
                )
                e.punteggio += 15
                return _altri_filtri(a, req, b, e, ctx)
            if len(con_sigla) == len(sedi):
                raise Escluso(f"Bando per altre province ({', '.join(province[:3])})")
    if req.territorio == "nazionale":
        e.soddisfatti.append("Bando nazionale")
        e.punteggio += 5
    elif req.regioni:
        dentro = next((s for s in sedi if s.regione and s.regione in req.regioni), None)
        tutte_note = bool(sedi) and all(s.regione for s in sedi)
        if dentro is None and tutte_note:
            raise Escluso(f"Bando per altre regioni ({', '.join(req.regioni[:3])})")
        if _ente_di_altre_province(a, b, e, ctx):
            pass  # bando di una Camera di commercio, Provincia o Comune di altre province
        elif dentro is None:
            e.da_verificare.append(f"Territorio ({', '.join(req.regioni[:3])}): manca la sede")
            e.punteggio -= 5
        elif req.territorio == "regionale":
            e.soddisfatti.append(f"Bando per la regione {_della(dentro)} ({dentro.regione})")
            e.punteggio += 15
        else:
            e.da_verificare.append(f"Solo alcuni comuni o province della regione {dentro.regione}")
            e.punteggio -= 10
    else:
        locale = ente_locale(b)
        if locale:
            # Ente locale con territorio non indicato: quasi sempre vale solo lì. Non si
            # esclude (potrebbe essere la sede), ma il punteggio scende sotto la soglia.
            e.da_verificare.append(f"Bando di un ente locale ({locale}): verificare il territorio")
            e.punteggio -= 20
        else:
            e.da_verificare.append("Territorio del bando non indicato")
            e.punteggio -= 5
    return _altri_filtri(a, req, b, e, ctx)


PROVINCIALI = ("camera di commercio", "cciaa", "provincia di", "comune di", "citta metropolitana")
ENTI_LOCALI = ("comune di ", "provincia di ", "citta metropolitana", "unione dei comuni",
               "camera di commercio", "cciaa ", "gal ", "regione ")  # fmt: skip


def ente_locale(b: Bando) -> str | None:
    """Nome dell'ente locale che emette il bando (dall'ente o dall'inizio del titolo)."""
    from app.utils.ricerca import normalizza

    for testo in (getattr(b, "ente", None) or "", (b.titolo or "")[:60]):
        n = normalizza(testo).strip()
        if any(n.startswith(x) for x in ENTI_LOCALI):
            return testo.strip()[:60]
    return None


def _comune_ammesso(sede: Any, comuni: list[str], ctx: Contesto) -> bool:
    """Il comune della sede è tra quelli del bando? Con la tabella ISTAT si confrontano i
    codici (nomi scritti in modi diversi, omonimi in province diverse); i nomi del bando non
    riconosciuti si confrontano per nome, come prima."""
    from app.riferimenti.istat import norm_nome

    t = ctx.territorio
    codice = getattr(sede, "codice_istat", None)
    if t is not None and not codice:
        voce = t.comune(sede.comune_testo, getattr(sede, "provincia", None))
        codice = voce.codice if voce else None
    nome_sede = norm_nome(sede.comune_testo)
    for nome in comuni:
        codici = t.codici(nome) if t is not None else None
        if codici and codice:
            if codice in codici:
                return True
        elif norm_nome(nome) == nome_sede:
            return True
    return False


# Bandi senza beneficiari indicati che dal titolo non sono per le imprese (fonti regionali
# come Lazio Europa pubblicano anche bandi per scuole, famiglie, cittadini ed enti)
PAROLE_NON_IMPRESE = (
    "studenti", "scuol", "istituti scolastici", "famiglie", "cittadini", "adozion",
    "prima infanzia", "presepi", "comuni del lazio", "enti locali", "universita",
    "borse di studio", "associazioni sportive", "parrocchi", "residenti", "istruzione",
    "filiera formativa", "iefp", "its academy", "enti di formazione", "organismi di formazione",
    "subito violenza", "vittime di",
)  # fmt: skip


def _ente_di_altre_province(a: Azienda, b: Bando, e: Esito, ctx: Contesto) -> bool:
    """Bandi di Camere di commercio, Province e Comuni registrati come «regionali» dalla
    fonte (es. CCIAA Frosinone-Latina con territorio «Lazio», trovato verificando il sito
    ufficiale): valgono di solito solo per le loro province. Con la tabella ISTAT si
    ricavano le province dal nome dell'ente; se la sede è altrove il punteggio scende sotto
    la soglia (non si esclude: va verificato sul bando). Conta ogni sede, anche operativa.
    True se nessuna sede è in quelle province."""
    t = ctx.territorio
    sigle_sedi = province_sedi(a)
    locale = ente_locale(b)
    if t is None or not sigle_sedi or not locale:
        return False
    from app.utils.ricerca import normalizza

    testo = normalizza(locale)
    # Solo enti provinciali o comunali: «Regione Friuli Venezia Giulia» non è la provincia di
    # Venezia, «Regione Molise» non è il comune di Molise; GAL e unioni non hanno un nome sicuro
    if not testo.startswith(PROVINCIALI):
        return False
    for prefisso in (
        "camera di commercio",
        "cciaa",
        "provincia di",
        "comune di",
        "citta metropolitana di",
    ):
        testo = testo.replace(prefisso, " ")
    sigle: set[str] = set()
    for parola in re.split(r"[-–,/]| e ", testo):
        parola = parola.strip(" .")
        if not parola:
            continue
        parole = parola.split()
        for i in range(len(parole)):  # «i.a.a. frosinone» → prova anche «frosinone»
            candidato = " ".join(parole[i:])
            sigla = t.sigla(candidato)
            if sigla is None:
                comune = t.comune(candidato)
                sigla = comune.sigla if comune else None
            if sigla:
                sigle.add(sigla)
                break
    # nomi di provincia scritti senza separatori («Arezzo Siena»)
    from app.riferimenti.istat import norm_nome

    piano = f" {norm_nome(testo)} "
    for nome, sigla in t.sigla_da_provincia.items():
        if len(nome) >= 4 and f" {nome} " in piano:
            sigle.add(sigla)
    if not sigle:
        return False
    if sigle_sedi & sigle:
        e.soddisfatti.append(f"Ente della provincia di una sede ({locale})")
        e.punteggio += 10
        return False
    e.da_verificare.append(
        f"Bando di {locale}: di solito solo per le imprese di {', '.join(sorted(sigle))}"
    )
    e.punteggio -= 25
    return True


def _tipologie(a: Azienda, b: Bando, req: Any, e: Esito) -> None:
    """Chi può partecipare, oltre il filtro «non per imprese»: bandi solo per
    professionisti, solo per startup o PMI innovative, solo per imprese da costituire."""
    from app.utils.ricerca import normalizza

    tip = [x.lower() for x in (req.tipologie_soggetto or [])]
    if not tip:
        titolo = f" {normalizza(b.titolo or '')} "
        if any(p in titolo for p in PAROLE_NON_IMPRESE):
            e.da_verificare.append("Beneficiari non indicati: dal titolo non sembra per imprese")
            e.punteggio -= 30
        return
    impresa = [
        x
        for x in tip
        if "impresa" in x or any(s in x for s in ("rete", "consorzio", "cooperative"))
    ]
    famiglia = famiglia_forma(getattr(a, "forma_giuridica", None))
    if not impresa:  # solo professionisti (e magari cittadini o enti)
        if famiglia in ("società di capitali", "società di persone", "cooperativa", "consorzio"):
            raise Escluso("Bando solo per liberi professionisti")
        if famiglia != "professionista":
            e.da_verificare.append("Bando per liberi professionisti")
            e.punteggio -= 15
        return
    generiche = [
        x for x in impresa if x in ("impresa", "rete d'impresa", "consorzio") or "cooperative" in x
    ]
    if not generiche:
        if all("da costituire" in x for x in impresa):
            e.da_verificare.append("Solo per imprese da costituire")
            e.punteggio -= 20
        elif all("innovativa" in x for x in impresa):
            e.da_verificare.append("Solo startup o PMI innovative")
            e.punteggio -= 15
        else:
            e.da_verificare.append("Beneficiari: " + ", ".join(req.tipologie_soggetto[:3]))
            e.punteggio -= 10


def _settori_fonte(a: Azienda, b: Bando, e: Esito) -> None:
    """Settori ammessi indicati dalla fonte nazionale (Settore_Attivita) contro l'ATECO."""
    from app.opportunita.settori import SETTORI, settori_possibili

    ammessi = (getattr(b, "raw", None) or {}).get("Settore_Attivita") or []
    if not ammessi or len(set(ammessi)) >= len(SETTORI):
        return
    primario = a.ateco_primario
    possibili = settori_possibili(primario.codice if primario is not None else None)
    if possibili is None:
        e.da_verificare.append("Settori ammessi: " + ", ".join(ammessi[:4]))
        return
    comuni = possibili & set(ammessi)
    if not comuni and len(set(ammessi)) <= 2:
        # Con uno o due settori la fonte spesso indica il tipo di intervento, non chi può
        # partecipare (es. Conto Termico 3.0: «Edilizia», ma vale per qualsiasi impresa che
        # fa lavori di efficienza energetica). Trovato con il collaudo: non si esclude.
        e.da_verificare.append(
            "Settori indicati dalla fonte: "
            + ", ".join(ammessi)
            + " (verificare se riguardano l'impresa o il tipo di intervento)"
        )
        return
    if not comuni:
        raise Escluso(f"Settore dell'ATECO {primario.codice} non tra quelli ammessi")
    e.soddisfatti.append("Settore ammesso: " + ", ".join(sorted(comuni)))
    e.punteggio += 10


AVVIO = re.compile(
    r"fase di avvi|avviamento|nuove imprese|imprese di nuova costituzione|da costituire|"
    r"costituite da (?:non pi[uù] di|meno di)|start[- ]?up",
    re.IGNORECASE,
)
CRISI = re.compile(
    r"(?:aziende|imprese) in (?:crisi|difficolt)|rischio di delocalizzazione|"
    r"crisi d'impresa|crisi aziendale",
    re.IGNORECASE,
)
SOLO_MICRO = re.compile(r"micro[ -]?imprese", re.IGNORECASE)
ALTRE_DIMENSIONI = re.compile(r"piccol[ae]|medi[ae]|PMI", re.IGNORECASE)


def _segnali_nel_testo(a: Azienda, b: Bando, e: Esito, oggi: date | None) -> None:
    """Indizi nella descrizione del bando che i dati strutturati della fonte non riportano
    (es. «Nuovo Fondo Futuro»: la fonte indica micro, piccole e medie, ma il testo parla di
    microimprese in fase di avviamento). Non escludono: abbassano il punteggio e lo dicono."""
    from app.utils.impresa import anzianita_mesi, dimensione_effettiva

    testo = (getattr(b, "descrizione", None) or "")[:4000]
    if not testo:
        return
    if AVVIO.search(testo):
        mesi = anzianita_mesi(getattr(a, "data_costituzione", None), oggi)
        if mesi is None:
            e.da_verificare.append("Il testo parla di imprese nuove o in avvio")
            e.punteggio -= 10
        elif mesi > 60:
            e.da_verificare.append(
                f"Il testo parla di imprese nuove o in avvio: l'impresa ha {mesi // 12} anni"
            )
            e.punteggio -= 40
    if CRISI.search(f"{getattr(b, 'titolo', '')} {testo[:600]}"):
        e.da_verificare.append("Bando per imprese in crisi o in difficoltà")
        e.punteggio -= 20
    dimensione = dimensione_effettiva(a)[0] if hasattr(a, "dipendenti") else a.dimensione_manuale
    if (
        dimensione in ("piccola", "media", "grande")
        and SOLO_MICRO.search(testo)
        and not ALTRE_DIMENSIONI.search(testo)
    ):
        e.da_verificare.append(f"Il testo parla solo di microimprese (impresa {dimensione})")
        e.punteggio -= 20


def _apertura_vecchia(b: Bando, e: Esito, oggi: date | None) -> None:
    """Aperto da più di 18 mesi senza data di chiusura: forse la fonte non l'ha aggiornato."""
    oggi = oggi or date.today()
    if b.stato == "aperto" and getattr(b, "data_chiusura", None) is None:
        apertura = getattr(b, "data_apertura", None)
        if apertura is not None and (oggi - apertura).days > 548:
            e.da_verificare.append(
                f"Aperto dal {apertura:%d/%m/%Y} senza data di chiusura: verificare che sia attivo"
            )
            e.punteggio -= 10


def _altri_filtri(a: Azienda, req: Any, b: Bando, e: Esito, ctx: Contesto) -> Esito:
    from app.utils.impresa import anzianita_mesi, dimensione_calcolata, plafond_residuo

    # Dimensione: quella manuale esclude; quella stimata (senza ULA) solo «da verificare»
    if req.dimensioni:
        manuale = a.dimensione_manuale
        stimata = None
        if not manuale:
            stimata = dimensione_calcolata(
                getattr(a, "dipendenti", None),
                getattr(a, "fatturato", None),
                getattr(a, "totale_attivo", None),
            )
        if manuale:
            if manuale not in req.dimensioni:
                raise Escluso(f"Dimensione {manuale} non ammessa ({', '.join(req.dimensioni)})")
            e.soddisfatti.append(f"Dimensione ammessa ({manuale})")
            e.punteggio += 10
        elif stimata in req.dimensioni:
            e.soddisfatti.append(f"Dimensione stimata ammessa ({stimata})")
            e.punteggio += 5
        else:
            nota = f" (stimata: {stimata})" if stimata else ""
            e.da_verificare.append(f"Dimensioni ammesse: {', '.join(req.dimensioni)}{nota}")
    from app.riferimenti.ateco import confronta

    primario = a.ateco_primario
    versione_az = getattr(primario, "versione", None) if primario is not None else None
    versione_bando = getattr(req, "ateco_versione", None)
    if req.ateco_ammessi:
        if primario is None:
            e.da_verificare.append("Il bando ammette solo alcuni ATECO: manca quello del cliente")
            e.punteggio -= 5
        else:
            esito = confronta(
                primario.codice, versione_az, req.ateco_ammessi, versione_bando, ctx.raccordo
            )
            if esito == "si":
                e.soddisfatti.append(f"ATECO {primario.codice} tra gli ammessi")
                e.punteggio += 20
            elif esito == "forse":
                e.da_verificare.append(
                    f"ATECO {primario.codice}: verificare con il raccordo ATECO 2007/2025"
                )
            else:
                raise Escluso(f"ATECO {primario.codice} non tra gli ammessi")
    esclusi = getattr(req, "ateco_esclusi", None)
    if esclusi and primario is not None:
        esito = confronta(primario.codice, versione_az, esclusi, versione_bando, ctx.raccordo)
        if esito == "si":
            raise Escluso(f"ATECO {primario.codice} escluso dal bando")
        if esito == "forse":
            e.da_verificare.append(
                f"ATECO {primario.codice} forse tra gli esclusi (raccordo ATECO 2007/2025)"
            )
    # Forma giuridica: solo quando il bando è esplicito e le forme sono riconosciute
    forme = getattr(req, "forme_giuridiche", None)
    if forme:
        mia = famiglia_forma(getattr(a, "forma_giuridica", None))
        ammesse = {famiglia_forma(f) for f in forme}
        if mia is None or None in ammesse:
            e.da_verificare.append(f"Forme giuridiche ammesse: {', '.join(forme[:4])}")
        elif mia in ammesse:
            e.soddisfatti.append(f"Forma giuridica ammessa ({a.forma_giuridica})")
        else:
            raise Escluso(f"Forma giuridica {a.forma_giuridica} non ammessa")
    # Anzianità dalla data di costituzione
    mesi = anzianita_mesi(getattr(a, "data_costituzione", None), ctx.oggi)
    massimo = getattr(req, "anzianita_max_mesi", None)
    minimo = getattr(req, "anzianita_min_mesi", None)
    if massimo or minimo:
        if mesi is None:
            limite = f"da meno di {massimo} mesi" if massimo else f"da almeno {minimo} mesi"
            e.da_verificare.append(f"Solo imprese costituite {limite}: manca la data")
            e.punteggio -= 5
        elif massimo and mesi > massimo:
            raise Escluso(f"Costituita da {mesi} mesi, il bando ne ammette al massimo {massimo}")
        elif minimo and mesi < minimo:
            raise Escluso(f"Costituita da {mesi} mesi, il bando ne chiede almeno {minimo}")
        else:
            e.soddisfatti.append(f"Anzianità compatibile ({mesi} mesi)")
    # De minimis: plafond noto (indicato a mano o con aiuti registrati) contro l'aiuto minimo
    if getattr(req, "regime", None) == "de_minimis":
        manuale = getattr(a, "plafond_de_minimis_manuale", None)
        if manuale is None and not ctx.aiuti:
            e.da_verificare.append("Aiuto in de minimis: plafond del cliente non noto")
        else:
            p = plafond_residuo(ctx.aiuti, "generale", manuale, ctx.oggi)
            minimo_aiuto = getattr(req, "importo_min", None)
            if minimo_aiuto is not None and p.residuo < minimo_aiuto:
                raise Escluso(
                    f"Plafond de minimis residuo {p.residuo:.0f} € inferiore all'aiuto minimo "
                    f"{minimo_aiuto:.0f} €"
                )
            e.soddisfatti.append(f"De minimis: plafond residuo stimato {p.residuo:.0f} €")
    # Spese ammissibili contro i progetti del cliente
    categorie = getattr(req, "categorie_spesa", None)
    if categorie:
        attivi = [p for p in ctx.progetti if p.attivo and p.categorie]
        migliore = max(attivi, key=lambda p: len(set(p.categorie) & set(categorie)), default=None)
        if migliore is not None and set(migliore.categorie) & set(categorie):
            comuni = sorted(set(migliore.categorie) & set(categorie))
            e.soddisfatti.append(f"Progetto «{migliore.titolo}»: spese {', '.join(comuni)}")
            e.punteggio += 10
            e.progetto_id = migliore.id
        else:
            e.soddisfatti.append("Spese ammissibili: " + ", ".join(categorie[:4]))
            if attivi:
                e.da_verificare.append("Le spese del bando non coincidono con i progetti")
                e.punteggio -= 5
    _settori_fonte(a, b, e)
    _apertura_vecchia(b, e, ctx.oggi)
    _segnali_nel_testo(a, b, e, ctx.oggi)
    if b.stato == "in_arrivo":
        e.soddisfatti.append("In arrivo: c'è tempo per preparare la domanda")
    return e


def _classifica_copre(classifica: str | None, importo: Decimal | None) -> bool | None:
    """True/False se si può dire, None se manca la classifica o l'importo."""
    if not classifica or classifica not in CLASSIFICHE_SOA or importo is None:
        return None
    limite = CLASSIFICHE_SOA[classifica]
    return True if limite is None else importo <= limite * INCREMENTO_QUINTO


def valuta_appalto(a: Azienda, ap: Appalto, regioni: set[str]) -> Esito | None:
    e = Esito(punteggio=40)
    # Territorio
    if ap.regioni:
        comuni = regioni & set(ap.regioni)
        if regioni and not comuni:
            return None
        if comuni:
            e.soddisfatti.append(f"Gara in {', '.join(sorted(comuni))}")
            e.punteggio += 10
        else:
            e.da_verificare.append("Regioni di interesse del cliente non indicate")
    else:
        e.soddisfatti.append("Gara valida in tutta Italia")
    # Categoria: SOA o CPV
    pertinente = False
    possedute = {s["codice"]: s.get("classifica") for s in a.soa_possedute or [] if s.get("codice")}
    if possedute and ap.categorie_soa:
        from app.appalti.scheda import prevalente

        prev = prevalente(ap.categorie_soa)
        if prev and prev["codice"] in possedute:
            pertinente = True
            importo = Decimal(str(prev["importo"])) if prev.get("importo") else ap.importo_stimato
            copre = _classifica_copre(possedute[prev["codice"]], importo)
            if copre is True:
                e.soddisfatti.append(
                    f"SOA {prev['codice']} classifica {possedute[prev['codice']]} sufficiente"
                )
                e.punteggio += 35
            elif copre is False:
                e.da_verificare.append(
                    f"SOA {prev['codice']}: classifica {possedute[prev['codice']]} insufficiente "
                    "per l'importo (valutare RTI o avvalimento)"
                )
                e.punteggio += 10
            else:
                e.soddisfatti.append(f"SOA prevalente {prev['codice']} posseduta")
                e.punteggio += 25
            for c in ap.categorie_soa:
                if c is not prev and c.get("codice") not in possedute:
                    e.da_verificare.append(
                        f"Scorporabile {c['codice']} non posseduta (subappalto o RTI)"
                    )
        elif prev is None and any(c["codice"] in possedute for c in ap.categorie_soa):
            pertinente = True
            e.da_verificare.append("Categoria prevalente non indicata: verificare il bando")
            e.punteggio += 10
    elif possedute and ap.natura == ["lavori"] and not ap.categorie_soa:
        e.da_verificare.append("Lavori: categorie SOA non indicate nella fonte")
    # CPV: conta soprattutto la categoria principale (il primo codice dell'avviso)
    interesse = set(a.cpv_interesse or [])
    principale = (ap.cpv or [""])[0][:2]
    secondarie = interesse & set(ap.divisioni_cpv or []) - {principale}
    from app.utils.cpv import DIVISIONI_CPV

    if principale in interesse:
        pertinente = True
        e.soddisfatti.append(f"Categoria principale di interesse: {DIVISIONI_CPV[principale]}")
        e.punteggio += 25
    elif secondarie and not pertinente:
        pertinente = True
        e.da_verificare.append(
            "Categoria di interesse solo secondaria: "
            + ", ".join(DIVISIONI_CPV.get(c, c) for c in sorted(secondarie))
        )
        e.punteggio += 5
    if not pertinente:
        return None
    if ap.scadenza and (ap.scadenza - date.today()).days < 7:
        e.da_verificare.append("Scade entro 7 giorni")
        e.punteggio -= 5
    return e


def _salva(
    db: Session,
    azienda: Azienda,
    trovate: list[tuple[str, int, Esito]],
    esistenti: dict[tuple[str, int], Opportunita],
    ora: datetime,
) -> tuple[int, set[tuple[str, int]]]:
    nuove = 0
    tenute: set[tuple[str, int]] = set()
    for tipo, oggetto_id, esito in trovate:
        chiave = (tipo, oggetto_id)
        tenute.add(chiave)
        o = esistenti.get(chiave)
        punteggio = max(0, min(100, esito.punteggio))
        if o is None:
            o = Opportunita(
                azienda_id=azienda.id,
                bando_id=oggetto_id if tipo == "bando" else None,
                appalto_id=oggetto_id if tipo == "appalto" else None,
                punteggio=punteggio,
                punteggio_regole=punteggio,
                motivi=esito.motivi(),
                progetto_id=esito.progetto_id,
                stato="nuova",
                trovata_il=ora,
            )
            db.add(o)
            nuove += 1
        else:
            o.punteggio_regole = punteggio
            # Il punteggio finale è quello dell'AI se c'è, altrimenti quello delle regole
            o.punteggio = o.punteggio_ai if o.punteggio_ai is not None else punteggio
            o.motivi = esito.motivi()
            o.progetto_id = esito.progetto_id
    return nuove, tenute


def abbina(db: Session, oggi: date | None = None) -> str:
    """Aggiorna le opportunità di tutti i clienti con avvisi attivi."""
    from app.models import Commento
    from app.squadra.note import pulisci_orfane

    con_note = set(db.scalars(select(Commento.oggetto_id).where(Commento.oggetto == "opportunita")))
    oggi = oggi or date.today()
    ora = datetime.now(UTC)
    clienti = list(
        db.scalars(
            select(Azienda)
            .where(Azienda.avvisi_attivi, Azienda.stato_cliente != "ex")
            .options(selectinload(Azienda.sedi), selectinload(Azienda.ateco))
        )
    )
    bandi = list(
        db.scalars(
            select(Bando)
            .where(Bando.stato.in_(("aperto", "in_arrivo")), Bando.bando_canonico_id.is_(None))
            .options(selectinload(Bando.requisiti))
        )
    )
    appalti = list(
        db.scalars(
            select(Appalto).where(
                or_(
                    Appalto.scadenza >= oggi,
                    Appalto.scadenza.is_(None) & (Appalto.fonte == "consip"),
                )
            )
        )
    )
    from app.riferimenti import ateco as rif_ateco
    from app.riferimenti import territorio as rif_territorio

    territorio = rif_territorio.in_memoria(db)
    raccordo = rif_ateco.in_memoria(db)
    totale_nuove = 0
    for a in clienti:
        esistenti = {
            ("bando" if o.bando_id else "appalto", o.bando_id or o.appalto_id): o
            for o in db.scalars(select(Opportunita).where(Opportunita.azienda_id == a.id))
        }
        ctx = Contesto(
            aiuti=list(db.scalars(select(AiutoDeMinimis).where(AiutoDeMinimis.azienda_id == a.id))),
            progetti=list(db.scalars(select(Progetto).where(Progetto.azienda_id == a.id))),
            oggi=oggi,
            territorio=territorio,
            raccordo=raccordo,
        )
        trovate_b = [
            ("bando", b.id, es) for b in bandi if (es := valuta_bando(a, b, ctx)) is not None
        ]
        regioni = regioni_cliente(a)
        trovate_a = [
            ("appalto", ap.id, es)
            for ap in appalti
            if (es := valuta_appalto(a, ap, regioni)) is not None
        ]
        migliori = []
        for gruppo in (trovate_b, trovate_a):
            gruppo = [g for g in gruppo if g[2].punteggio >= PUNTEGGIO_MINIMO]
            gruppo.sort(key=lambda g: -g[2].punteggio)
            migliori += gruppo[:MAX_PER_TIPO]
        nuove, tenute = _salva(db, a, migliori, esistenti, ora)
        totale_nuove += nuove
        # Le opportunità non più valide spariscono, salvo quelle già lavorate o con note
        for chiave, o in esistenti.items():
            if chiave not in tenute and o.stato in ("nuova", "vista") and o.id not in con_note:
                db.delete(o)
    db.flush()
    pulisci_orfane(db)
    db.commit()
    clienti_txt = "1 cliente valutato" if len(clienti) == 1 else f"{len(clienti)} clienti valutati"
    nuove_txt = "1 nuova" if totale_nuove == 1 else f"{totale_nuove} nuove"
    return f"Opportunità: {clienti_txt}, {nuove_txt}."


def da_notificare(db: Session, soglia_ai: int = 0) -> list[Opportunita]:
    """Opportunità nuove non ancora segnalate. Quelle valutate dall'AI entrano nell'email
    solo se il punteggio AI raggiunge la soglia; le altre restano nella pagina."""
    return list(
        db.scalars(
            select(Opportunita)
            .where(
                Opportunita.notificata_il.is_(None),
                Opportunita.stato == "nuova",
                or_(Opportunita.punteggio_ai.is_(None), Opportunita.punteggio_ai >= soglia_ai),
            )
            .options(
                selectinload(Opportunita.azienda),
                selectinload(Opportunita.bando),
                selectinload(Opportunita.appalto),
            )
            .order_by(Opportunita.azienda_id, Opportunita.punteggio.desc())
        )
    )


def riepilogo_motivi(o: Opportunita) -> dict[str, Any]:
    return o.motivi or {"soddisfatti": [], "da_verificare": []}
