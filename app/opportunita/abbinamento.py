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

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Azienda, Bando
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
    (("turism", "ricettiv", "albergh", "alberghi"), ("55", "56", "79"), "turismo"),
    (("sport",), ("93",), "sport"),
)  # fmt: skip


def _sezione_settore(titolo: str) -> tuple[tuple[str, ...], str] | None:
    from app.utils.ricerca import normalizza

    t = f" {normalizza(titolo)} "
    for parole, divisioni, nome in SETTORI_BANDO:
        if any(p in t for p in parole):
            return divisioni, nome
    return None


@dataclass
class Esito:
    punteggio: int
    soddisfatti: list[str] = field(default_factory=list)
    da_verificare: list[str] = field(default_factory=list)

    def motivi(self) -> dict[str, list[str]]:
        return {"soddisfatti": self.soddisfatti, "da_verificare": self.da_verificare}


def regioni_cliente(a: Azienda) -> set[str]:
    regioni = set(a.regioni_interesse or [])
    if a.sede_legale and a.sede_legale.regione:
        regioni.add(a.sede_legale.regione)
    return regioni


def _ateco_ok(codice: str, ammessi: list[str]) -> bool:
    c = codice.replace(".", "")
    return any(c.startswith(x.replace(".", "")) for x in ammessi)


def valuta_bando(a: Azienda, b: Bando) -> Esito | None:
    req = b.requisiti
    e = Esito(punteggio=50)
    if req is None:
        e.da_verificare.append("Requisiti del bando non ancora disponibili")
        e.punteggio -= 10
        return e
    if req.tipologie_soggetto and not any(
        any(s in t.lower() for s in SOGGETTI_IMPRESA) for t in req.tipologie_soggetto
    ):
        return None
    # Bandi di settore (cultura, agricoltura, turismo…): solo per chi è di quel settore
    settore = _sezione_settore(b.titolo)
    if settore:
        divisioni, nome = settore
        primario = a.ateco_primario
        if primario is not None:
            if primario.codice.replace(".", "")[:2] not in divisioni:
                return None
            e.soddisfatti.append(f"Bando per il settore {nome}, coerente con l'ATECO")
            e.punteggio += 10
        else:
            e.da_verificare.append(f"Bando per il settore {nome}")
            e.punteggio -= 10
    sede = a.sede_legale.regione if a.sede_legale else None
    comune = a.sede_legale.comune_testo if a.sede_legale else None
    if req.territorio == "comunale" and req.comuni and comune:
        from app.utils.ricerca import normalizza

        if normalizza(comune) not in {normalizza(c) for c in req.comuni}:
            return None
        e.soddisfatti.append(f"Bando per il comune della sede ({comune})")
        e.punteggio += 20
        return _dimensione_ateco(a, req, b, e)
    if req.territorio == "nazionale":
        e.soddisfatti.append("Bando nazionale")
        e.punteggio += 5
    elif req.regioni:
        if sede is None:
            e.da_verificare.append(f"Territorio ({', '.join(req.regioni[:3])}): manca la sede")
            e.punteggio -= 5
        elif sede in req.regioni:
            if req.territorio == "regionale":
                e.soddisfatti.append(f"Bando per la regione della sede ({sede})")
                e.punteggio += 15
            else:
                e.da_verificare.append(f"Solo alcuni comuni o province della regione {sede}")
                e.punteggio -= 10
        else:
            return None
    else:
        e.da_verificare.append("Territorio del bando non indicato")
        e.punteggio -= 5
    return _dimensione_ateco(a, req, b, e)


def _dimensione_ateco(a: Azienda, req: Any, b: Bando, e: Esito) -> Esito | None:
    if req.dimensioni:
        if a.dimensione_manuale:
            if a.dimensione_manuale not in req.dimensioni:
                return None
            e.soddisfatti.append(f"Dimensione ammessa ({a.dimensione_manuale})")
            e.punteggio += 10
        else:
            e.da_verificare.append(f"Dimensioni ammesse: {', '.join(req.dimensioni)}")
    if req.ateco_ammessi:
        primario = a.ateco_primario
        if primario is None:
            e.da_verificare.append("Il bando ammette solo alcuni ATECO: manca quello del cliente")
            e.punteggio -= 5
        elif _ateco_ok(primario.codice, req.ateco_ammessi):
            e.soddisfatti.append(f"ATECO {primario.codice} tra gli ammessi")
            e.punteggio += 20
        else:
            return None
    primario = a.ateco_primario
    esclusi = getattr(req, "ateco_esclusi", None)
    if esclusi and primario is not None and _ateco_ok(primario.codice, esclusi):
        return None
    if getattr(req, "anzianita_max_mesi", None):
        e.da_verificare.append(f"Solo imprese costituite da meno di {req.anzianita_max_mesi} mesi")
        e.punteggio -= 5
    if getattr(req, "regime", None) == "de_minimis":
        e.da_verificare.append("Aiuto in de minimis: verificare il plafond del cliente")
    if getattr(req, "categorie_spesa", None):
        e.soddisfatti.append("Spese ammissibili: " + ", ".join(req.categorie_spesa[:4]))
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
    return nuove, tenute


def abbina(db: Session, oggi: date | None = None) -> str:
    """Aggiorna le opportunità di tutti i clienti con avvisi attivi."""
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
    totale_nuove = 0
    for a in clienti:
        esistenti = {
            ("bando" if o.bando_id else "appalto", o.bando_id or o.appalto_id): o
            for o in db.scalars(select(Opportunita).where(Opportunita.azienda_id == a.id))
        }
        trovate_b = [("bando", b.id, es) for b in bandi if (es := valuta_bando(a, b)) is not None]
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
        # Le opportunità non più valide spariscono, salvo quelle già lavorate
        for chiave, o in esistenti.items():
            if chiave not in tenute and o.stato in ("nuova", "vista"):
                db.delete(o)
    db.commit()
    return f"Opportunità: {len(clienti)} clienti valutati, {totale_nuove} nuove."


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


def testo_email(opportunita: list[Opportunita], base_url: str = "") -> str:
    righe = ["Buongiorno,", "", "ecco le nuove opportunità trovate per i clienti dello studio:"]
    per_cliente: dict[int, list[Opportunita]] = {}
    for o in opportunita:
        per_cliente.setdefault(o.azienda_id, []).append(o)
    for lista in per_cliente.values():
        righe += ["", f"■ {lista[0].azienda.ragione_sociale}"]
        for o in lista[:10]:
            tipo = "Bando" if o.bando_id else "Appalto"
            scad = f" – scade il {o.scadenza:%d/%m/%Y}" if o.scadenza else ""
            fonte = "AI" if o.punteggio_ai is not None else "regole"
            righe.append(
                f"  • {tipo}: {o.titolo[:140]}{scad} (pertinenza {o.punteggio}/100, {fonte})"
            )
            if o.motivazione_ai:
                righe.append(f"    {o.motivazione_ai[:300]}")
        if len(lista) > 10:
            righe.append(f"  … e altre {len(lista) - 10}")
    righe += ["", "Le trovi tutte nella pagina Opportunità di Europrojecta Studio."]
    return "\n".join(righe)


def riepilogo_motivi(o: Opportunita) -> dict[str, Any]:
    return o.motivi or {"soddisfatti": [], "da_verificare": []}
