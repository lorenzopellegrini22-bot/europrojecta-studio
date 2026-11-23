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
# Clienti enti pubblici (azienda.tipo_soggetto): beneficiari della fonte che li comprendono
SOGGETTI_ENTE = ("ente pubblico", "enti pubblici", "pubblica amministrazione", "comuni",
                 "comune", "enti locali", "ente locale", "unioni di comuni")  # fmt: skip
# Bandi senza beneficiari indicati: parole del titolo che dicono a chi si rivolgono
TITOLO_ENTI = re.compile(
    r"\b(comuni|enti locali|enti pubblici|amministrazioni (comunali|locali|pubbliche)|"
    r"unioni (dei |di )?comuni|piccoli comuni|province)\b"
)
TITOLO_IMPRESE = re.compile(
    r"\b(imprese|impresa|pmi|startup|start-up|aziende|professionisti|lavoratori autonomi|"
    r"commercianti|esercizi commerciali|artigian\w*|imprenditori\w*|autoimpiego|automotive|"
    r"assunzion\w*|filiera|filiere)\b"
)


ALTRI_ENTI = re.compile(
    r"\b(aler|ater|ats|asst|asl|aziende sanitarie|aziende ospedaliere|universita|atenei|"
    r"camere di commercio|cciaa|istituti scolastici|scuole paritarie|parrocchie)\b"
)


def _migliaia(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def e_ente(a: Any) -> bool:
    return getattr(a, "tipo_soggetto", None) == "ente_pubblico"


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
    # Punteggio massimo, quando il bando quasi certamente non vale per il cliente ma non si
    # può escludere (es. Camera di commercio di un'altra provincia): resta sotto la soglia
    tetto: int | None = None

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


def limite_per_tipo(a: Azienda) -> int:
    """Quante opportunità tenere per tipo: con sedi in più regioni il limite cresce, così i
    bandi della regione di una sede operativa non tolgono il posto a quelli della sede legale."""
    extra = max(len(regioni_sedi(a)) - 1, 0)
    return min(MAX_PER_TIPO + 15 * extra, MAX_PER_TIPO * 2)


def _della(sede: Any) -> str:
    return "della sede" if sede.tipo == "legale" else "della sede operativa"


def regioni_cliente(a: Azienda) -> set[str]:
    """Regioni per gli appalti: quelle delle sedi più le regioni di interesse."""
    return set(a.regioni_interesse or []) | regioni_sedi(a)


DIVISIONI_ATECO_AMPIO = 60  # su 88: oltre, il bando è aperto a quasi tutti i settori


def _ateco_ampio(ammessi: list[str]) -> bool:
    return len({c.replace(".", "")[:2] for c in ammessi}) >= DIVISIONI_ATECO_AMPIO


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
        esito = _valuta(a, b, contesto or Contesto())
    except Escluso as e:
        return None, str(e)
    if esito.tetto is not None:
        esito.punteggio = min(esito.punteggio, esito.tetto)
    return esito, None


def _valuta(a: Azienda, b: Bando, ctx: Contesto) -> Esito:
    req = b.requisiti
    e = Esito(punteggio=50)
    if req is None:
        e.da_verificare.append("Requisiti del bando non ancora disponibili")
        e.punteggio -= 10
        return e
    ente = e_ente(a)
    if ente:
        _beneficiari_ente(a, b, req, e)
    else:
        if req.tipologie_soggetto and not any(
            any(s in t.lower() for s in SOGGETTI_IMPRESA) for t in req.tipologie_soggetto
        ):
            raise Escluso(
                "Bando non rivolto alle imprese: " + ", ".join(req.tipologie_soggetto[:3])
            )
        _tipologie(a, b, req, e)
    # Bandi di settore (cultura, agricoltura, turismo…): solo per chi è di quel settore
    settore = None if ente else _sezione_settore(b.titolo)
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
        if locale and _ente_di_altre_province(a, b, e, ctx):
            pass  # es. «Comune di Cividale del Friuli» per un negozio di Padova: sotto soglia
        elif locale:
            # Ente locale con territorio non indicato: quasi sempre vale solo lì. Non si
            # esclude (potrebbe essere la sede), ma il punteggio scende sotto la soglia.
            e.da_verificare.append(f"Bando di un ente locale ({locale}): verificare il territorio")
            e.punteggio -= 20
        else:
            e.da_verificare.append("Territorio del bando non indicato")
            e.punteggio -= 5
    return _altri_filtri(a, req, b, e, ctx)


# «Provincia autonoma di»: Trento e Bolzano sono nella stessa regione ma hanno bandi propri
# (120 bandi aperti il 21/11/2026; quelli di Bolzano arrivavano a un ristorante di Trento)
PROVINCIALI = ("camera di commercio", "cciaa", "provincia di", "provincia autonoma di",
               "comune di", "citta metropolitana")  # fmt: skip
ENTI_LOCALI = ("comune di ", "provincia di ", "provincia autonoma di ", "citta metropolitana",
               "unione dei comuni",
               "camera di commercio", "cciaa ", "gal ", "regione ")  # fmt: skip


def ente_locale(b: Bando) -> str | None:
    """Nome dell'ente locale che emette il bando (dall'ente o dall'inizio del titolo).
    Il nome dell'ente resta intero: «Camera di Commercio, Industria, Artigianato e Agricoltura
    di Brescia» tagliato a 60 caratteri perdeva la provincia (trovato il 21/11/2026)."""
    from app.utils.ricerca import normalizza

    for testo in (getattr(b, "ente", None) or "", (b.titolo or "")[:60]):
        n = normalizza(testo).strip()
        if any(n.startswith(x) for x in ENTI_LOCALI):
            return testo.strip()[:120]
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

    # «CCIAA Firenze: voucher digitali»: dopo i due punti c'è l'oggetto, non l'ente
    testo = normalizza(locale).split(":")[0]
    # Solo enti provinciali o comunali: «Regione Friuli Venezia Giulia» non è la provincia di
    # Venezia, «Regione Molise» non è il comune di Molise; GAL e unioni non hanno un nome sicuro
    if not testo.startswith(PROVINCIALI):
        return False
    for prefisso in (
        "camera di commercio",
        "cciaa",
        "provincia autonoma di",
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
        f"Bando di {locale}: di solito solo per chi è in {', '.join(sorted(sigle))}"
    )
    e.punteggio -= 25
    # Collaudo su 50 aziende (21/11/2026): con i bonus di settore, ATECO e progetto il voucher
    # turismo della CCIAA di Bari arrivava a 75 per un hotel di Lecce
    e.tetto = PUNTEGGIO_MINIMO - 5
    return True


def _beneficiari_ente(a: Azienda, b: Bando, req: Any, e: Esito) -> None:
    """Cliente ente pubblico (comune, provincia, unione…): passano i bandi che hanno gli enti
    tra i beneficiari; quelli solo per imprese, professionisti o cittadini si escludono.
    «Ente Pubblico» della fonte nazionale comprende anche ASL, Camere di commercio e aziende
    per la casa: un bando aperto a molti tipi di soggetti resta da verificare."""
    from app.utils.enti import limite_abitanti
    from app.utils.ricerca import normalizza

    titolo = f" {normalizza(b.titolo or '')} "
    # Bando pubblicato dallo stesso ente (prova del 23/11/2026: «Bando riqualificazione
    # caseificio - Comune di Pedavena» arrivava al Comune di Pedavena con 90)
    nome = normalizza(getattr(a, "ragione_sociale", None) or "").strip()
    if len(nome) > 10 and nome in f"{normalizza(getattr(b, 'ente', None) or '')} {titolo}":
        e.da_verificare.append("Bando pubblicato dallo stesso ente: non è un finanziamento per lui")
        e.tetto = PUNTEGGIO_MINIMO - 5
    # «Ente Pubblico» comprende aziende per la casa, sanità, università e Camere di commercio:
    # i bandi Aler della Regione Lombardia arrivavano ai comuni con 85 (prova 23/11/2026)
    altro = ALTRI_ENTI.search(titolo)
    if altro and not TITOLO_ENTI.search(titolo):
        e.da_verificare.append(f"Bando per {altro.group(1).upper()}: di solito non per i comuni")
        e.tetto = PUNTEGGIO_MINIMO - 5
    tip = req.tipologie_soggetto or []
    if tip:
        enti = [t for t in tip if any(s in normalizza(t) for s in SOGGETTI_ENTE)]
        if not enti:
            raise Escluso("Bando non rivolto agli enti pubblici: " + ", ".join(tip[:3]))
        if len(enti) == len(tip):
            e.soddisfatti.append("Bando riservato agli enti pubblici")
            e.punteggio += 20
        else:
            e.soddisfatti.append("Enti pubblici tra i beneficiari")
            if TITOLO_IMPRESE.search(titolo) and not TITOLO_ENTI.search(titolo):
                e.da_verificare.append("Il titolo parla di imprese: verificare il ruolo dell'ente")
                e.punteggio -= 25
                # «Dote Impresa» della Provincia di Sondrio restava a 50 per Sondrio
                e.tetto = PUNTEGGIO_MINIMO - 5
            elif len(tip) >= 4:
                e.da_verificare.append(
                    f"Aperto a {len(tip)} tipi di soggetti: verificare che valga per l'ente"
                )
                e.punteggio -= 5
    elif TITOLO_ENTI.search(titolo):
        e.soddisfatti.append("Dal titolo: rivolto a comuni o enti locali")
        e.punteggio += 10
    elif TITOLO_IMPRESE.search(titolo):
        e.da_verificare.append("Beneficiari non indicati: dal titolo sembra per le imprese")
        e.punteggio -= 30
    else:
        # Prova sui comuni di Viterbo (23/11/2026): senza beneficiari e senza enti nel titolo
        # passavano viaggi di istruzione, adozioni e autoimpiego della Regione Lazio
        e.da_verificare.append("Beneficiari non indicati: il titolo non parla di enti")
        e.punteggio -= 25
    # Popolazione: «piccoli comuni», «comuni fino a 5.000 abitanti». È un requisito certo solo
    # nel titolo: nel testo spesso cambia solo la percentuale di contributo (parcheggi urbani
    # nei Comuni del Lazio: 100% fino a 5.000 abitanti, 90% per gli altri; prova 23/11/2026)
    abitanti = getattr(a, "abitanti", None)
    limite = limite_abitanti(b.titolo)
    if limite:
        fino = f"fino a {_migliaia(limite)} abitanti"
        if abitanti is None:
            e.da_verificare.append(f"Solo comuni {fino}: indica gli abitanti del cliente")
        elif abitanti > limite:
            raise Escluso(f"Bando per comuni {fino} (il cliente ne ha {_migliaia(abitanti)})")
        else:
            e.soddisfatti.append(f"Comune {fino} ({_migliaia(abitanti)})")
            e.punteggio += 10
    elif limite := limite_abitanti((getattr(b, "descrizione", None) or "")[:3000]):
        e.da_verificare.append(
            f"Il testo cita i comuni fino a {_migliaia(limite)} abitanti: requisito o solo "
            "misura del contributo?"
        )


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
    if not ammessi or len(set(ammessi)) >= len(SETTORI) * 2 // 3:
        return  # tutti o quasi: non distingue i clienti
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
MESI_IMPRESA_GIOVANE = 36
SOLO_MICRO = re.compile(r"\bmicro[ -]?imprese\b", re.IGNORECASE)
ALTRE_DIMENSIONI = re.compile(r"\bpiccol[ae]\b|\bmedi[ae]\b|\bPMI\b", re.IGNORECASE)


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
        elif mesi <= MESI_IMPRESA_GIOVANE:
            # Smart&Start per una startup di pochi mesi era al 26° posto (prova del 21/11/2026)
            e.soddisfatti.append(f"Bando per imprese nuove: l'impresa ha {mesi} mesi")
            e.punteggio += 15
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


# Esigenze del cliente (i suoi progetti d'investimento) contro ciò che il bando finanzia.
# Le spese ammissibili (categorie_spesa) vengono dall'estrazione AI dei PDF e sono le più
# precise, ma ce l'hanno pochi bandi; quasi tutti hanno invece la «finalità» della fonte
# nazionale, e il titolo dice spesso cosa si finanzia («beni strumentali», «fiere»…).
# Prova del 21/11/2026 con un'officina lombarda che vuole comprare macchinari: senza questo
# la Nuova Sabatini era al 76° posto (si tengono i primi 30) dietro a bandi di formazione.
FINALITA_PER_CATEGORIA: dict[str, tuple[str, ...]] = {
    "macchinari e attrezzature": ("sostegno investimenti",),
    "digitale e software": ("digitalizzazione",),
    "ricerca e sviluppo": ("innovazione e ricerca",),
    "energia e ambiente": ("transizione ecologica",),
    "formazione": ("formazione",),
    "assunzioni": ("formazione",),  # «Formazione (lavoro, occupazione, riqualificazione…)»
    "internazionalizzazione": ("internazionalizzazione",),
    "immobili e opere": ("sostegno investimenti",),
    "avvio d'impresa": ("start up", "imprenditoria giovanile", "imprenditoria femminile"),
    "brevetti e proprieta intellettuale": ("innovazione e ricerca",),
}
PAROLE_PER_CATEGORIA: dict[str, re.Pattern[str]] = {
    c: re.compile(p)
    for c, p in {
        # non «impianti» da solo: fotovoltaici, sportivi… (FERX e Credito Sportivo finivano in
        # cima per chi voleva comprare macchinari, collaudo del 21/11/2026)
        "macchinari e attrezzature": r"beni strumentali|macchinar|attrezzatur|sabatini"
        r"|impianti (?:produttivi|industriali|e macchinari)"
        r"|\b[45]\.0\b|ammodernament",
        "digitale e software": r"digital|software|e.?commerce|cloud|cyber|\bict\b|informatic",
        # «startup innovative», «PMI innovative» (Smart&Start, collaudo del 21/11/2026)
        "ricerca e sviluppo": r"ricerca|sviluppo sperimentale|innovazion|innovativ",
        "energia e ambiente": r"energ|fotovolt|rinnovabil|efficientament|ecologic|\bgreen\b"
        r"|agrisolare|conto termico|decarbonizz|economia circolare",
        "formazione": r"formazion|competenze|riqualificazione professionale",
        "assunzioni": r"assunzion|occupazion|inserimento lavorativ|posti di lavoro"
        r"|giovani laureati",
        "internazionalizzazione": r"internazionalizz|\bexport|esportaz|mercati esteri|\bfiere?\b",
        "immobili e opere": r"ristrutturaz|riqualificazione (?:energetica|edilizia)|immobil"
        r"|opere murarie|edili",
        "avvio d'impresa": r"nuove imprese|nuova impresa|start ?up|\bavvio|autoimpiego"
        r"|autoimprenditorialit|da costituire|nuova imprenditorialit|resto al sud",
        "brevetti e proprieta intellettuale": r"brevett|marchi|proprieta intellettuale",
        "consulenze": r"consulenz|voucher|temporary manager|servizi qualificati",
    }.items()
}
# Punti per livello di certezza: spese ammissibili del bando, parole del titolo o della
# presentazione, sola finalità (nelle fonti è spesso generica: «Sostegno investimenti» ha un
# bando su tre). Senza legami con i progetti attivi il bando scende ma non viene escluso.
PUNTI_PROGETTO = {"spese": 30, "titolo": 30, "finalita": 5}
PUNTI_SENZA_PROGETTO = -20


def categorie_progetto(p: Any) -> set[str]:
    """Categorie di spesa del progetto: quelle scelte, oppure ricavate dal testo."""
    from app.utils.ricerca import normalizza

    scelte = {normalizza(c) for c in p.categorie or [] if c != "altro"}
    if scelte:
        return scelte
    testo = normalizza(f"{getattr(p, 'titolo', '') or ''} {getattr(p, 'descrizione', '') or ''}")
    return {c for c, r in PAROLE_PER_CATEGORIA.items() if r.search(testo)}


def finanzia(b: Bando, req: Any) -> dict[str, set[str]]:
    """Cosa finanzia il bando, per livello: «spese» (spese ammissibili), «titolo» (parole del
    titolo o della presentazione), «finalita» (finalità della fonte)."""
    from app.utils.ricerca import normalizza

    spese = {normalizza(c) for c in getattr(req, "categorie_spesa", None) or []}
    testo = normalizza(f"{b.titolo or ''} {(getattr(b, 'descrizione', None) or '')[:300]}")
    titolo = {c for c, r in PAROLE_PER_CATEGORIA.items() if r.search(testo)}
    finalita = [normalizza(f) for f in getattr(req, "finalita", None) or []]
    da_finalita = {
        c for c, ff in FINALITA_PER_CATEGORIA.items() if any(f.startswith(ff) for f in finalita)
    }
    return {"spese": spese, "titolo": titolo - spese, "finalita": da_finalita - spese - titolo}


def _esigenze(b: Bando, req: Any, e: Esito, ctx: Contesto) -> None:
    """Il bando finanzia qualcosa che il cliente vuole fare? Se il cliente ha progetti attivi,
    i bandi adatti salgono e gli altri scendono (non si esclude: le fonti sono incomplete)."""
    livelli = finanzia(b, req)
    spese = getattr(req, "categorie_spesa", None)
    if spese:
        e.soddisfatti.append("Spese ammissibili: " + ", ".join(spese[:4]))
    progetti = [(p, categorie_progetto(p)) for p in ctx.progetti if p.attivo]
    progetti = [(p, c) for p, c in progetti if c]
    if not progetti:
        return
    migliore, comuni, punti = None, set(), 0
    for p, cat in progetti:
        for livello, quante in livelli.items():
            if cat & quante:
                comuni_p = cat & (livelli["spese"] | livelli["titolo"] | livelli["finalita"])
                if (PUNTI_PROGETTO[livello], len(comuni_p)) > (punti, len(comuni)):
                    migliore, comuni, punti = p, comuni_p, PUNTI_PROGETTO[livello]
                break
    if migliore is None:
        e.da_verificare.append("Non risulta collegato ai progetti del cliente")
        e.punteggio += PUNTI_SENZA_PROGETTO
        return
    from app.models.crm_esteso import CATEGORIE_SPESA
    from app.utils.ricerca import normalizza

    nomi = {normalizza(c): c for c in CATEGORIE_SPESA}  # con gli accenti, per la pagina
    elenco = ", ".join(sorted(nomi.get(c, c) for c in comuni))
    if punti == PUNTI_PROGETTO["finalita"]:
        e.da_verificare.append(
            f"Forse utile al progetto «{migliore.titolo}» ({elenco}): lo dice solo la finalità"
        )
    else:
        e.soddisfatti.append(f"Adatto al progetto «{migliore.titolo}» ({elenco})")
    e.punteggio += punti
    e.progetto_id = migliore.id


def _altri_filtri(a: Azienda, req: Any, b: Bando, e: Esito, ctx: Contesto) -> Esito:
    from app.utils.impresa import anzianita_mesi, dimensione_calcolata, plafond_residuo

    if e_ente(a):
        # ATECO, dimensione, forma giuridica, anzianità e de minimis riguardano le imprese
        _esigenze(b, req, e, ctx)
        _apertura_vecchia(b, e, ctx.oggi)
        if b.stato == "in_arrivo":
            e.soddisfatti.append("In arrivo: c'è tempo per preparare la domanda")
        return e
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
            if esito == "si" and _ateco_ampio(req.ateco_ammessi):
                # aperto a quasi tutti i settori: l'ATECO ammesso non dice molto
                e.soddisfatti.append(f"ATECO {primario.codice} ammesso (quasi tutti i settori)")
                e.punteggio += 5
            elif esito == "si":
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
    _esigenze(b, req, e, ctx)
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
    # CPV: conta soprattutto la categoria principale (il primo codice dell'avviso). Gli
    # interessi possono essere divisioni (79) o codici più precisi (7971 = vigilanza): il
    # 22/11/2026 un istituto di vigilanza con la sola divisione 79 riceveva gare di grafica
    from app.utils.cpv import corrisponde, etichetta, interessi_effettivi

    interesse = interessi_effettivi(a.cpv_interesse)
    codici = list(ap.cpv or []) or list(ap.divisioni_cpv or [])
    principale = corrisponde(codici[0], interesse) if codici else None
    secondarie = {i for c in codici[1:] if (i := corrisponde(c, interesse))} - {principale}
    if principale:
        pertinente = True
        e.soddisfatti.append(f"Categoria principale di interesse: {etichetta(principale)}")
        e.punteggio += 25 + (5 if len(principale) > 2 else 0)
    elif secondarie and not pertinente:
        pertinente = True
        e.da_verificare.append(
            "Categoria di interesse solo secondaria: "
            + ", ".join(etichetta(c) for c in sorted(secondarie))
        )
        e.punteggio += 5
    if not pertinente:
        return None
    if ap.scadenza and (ap.scadenza - date.today()).days < 7:
        e.da_verificare.append("Scade entro 7 giorni")
        e.punteggio -= 5
    return e


def senza_titoli_ripetuti(
    gruppo: list[tuple[str, int, Esito]], titoli: dict[int, str]
) -> list[tuple[str, int, Esito]]:
    """Una sola opportunità per titolo: la stessa fonte a volte pubblica due schede della
    stessa misura con link diversi (Provincia di Bolzano, collaudo del 21/11/2026). Il
    gruppo è già ordinato: resta la scheda con il punteggio più alto."""
    from app.utils.ricerca import normalizza

    visti: set[str] = set()
    tenuti = []
    for g in gruppo:
        chiave = " ".join(re.findall(r"[a-z0-9]+", normalizza(titoli.get(g[1], ""))))
        if chiave and chiave in visti:
            continue
        visti.add(chiave)
        tenuti.append(g)
    return tenuti


def punteggio_finale(grezzo: int) -> int:
    """Il punteggio delle regole su 0-100. Sopra 80 si comprime invece di tagliare a 100:
    con progetto, settore, ATECO e regione un bando arriva a 150, e tagliando a 100 i migliori
    risultavano tutti pari (collaudo del 21/11/2026), perdendo l'ordine nella pagina."""
    import math

    if grezzo <= 80:
        return max(0, grezzo)
    return 80 + round(19 * (1 - math.exp(-(grezzo - 80) / 35)))


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
        punteggio = punteggio_finale(esito.punteggio)
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
            .where(
                Bando.stato.in_(("aperto", "in_arrivo")),
                Bando.bando_canonico_id.is_(None),
                # lo stato si ricalcola con la raccolta notturna: se quella salta, un bando
                # scaduto ieri non deve comparire tra le opportunità
                or_(Bando.data_chiusura.is_(None), Bando.data_chiusura >= oggi),
            )
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
    titoli = {b.id: b.titolo for b in bandi}
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
        # un ente pubblico le gare le bandisce, non vi partecipa
        trovate_a = [
            ("appalto", ap.id, es)
            for ap in ([] if e_ente(a) else appalti)
            if (es := valuta_appalto(a, ap, regioni)) is not None
        ]
        migliori = []
        for sono_bandi, gruppo in ((True, trovate_b), (False, trovate_a)):
            gruppo = [g for g in gruppo if g[2].punteggio >= PUNTEGGIO_MINIMO]
            gruppo.sort(key=lambda g: -g[2].punteggio)
            if sono_bandi:
                gruppo = senza_titoli_ripetuti(gruppo, titoli)
            migliori += gruppo[: limite_per_tipo(a)]
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
