"""Clienti: elenco, scheda, inserimento e modifica, referenti, compilazione dal Registro
Imprese (se l'integrazione è collegata e attiva).

Titolare e collaboratori gestiscono i clienti; solo il titolare può eliminarli (e solo se non
hanno pratiche)."""

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.auth.dipendenze import richiede_titolare, utente_corrente, verifica_csrf
from app.db import get_db
from app.integrazioni import servizio as integrazioni
from app.integrazioni.registro_imprese import ErroreRegistro, cerca
from app.models import (
    Allegato,
    Azienda,
    AziendaAteco,
    Pratica,
    Referente,
    Sede,
    Segnalatore,
    Utente,
)
from app.models.crm import DIMENSIONI, STATI_CLIENTE, TIPI_SOGGETTO
from app.models.crm_esteso import FREQUENZE_AGGIORNAMENTO, Tag
from app.opportunita.abbinamento import CLASSIFICHE_SOA
from app.pratiche.servizio import ETICHETTE_STATO_PRATICA, partita_iva_valida
from app.riferimenti.istat import norm_nome
from app.sicurezza.cifratura import ErroreCifratura
from app.utils.cpv import DIVISIONI_CPV
from app.utils.enti import tipo_dal_nome
from app.utils.numeri import importo_italiano
from app.utils.ricerca import condizione
from app.utils.territorio import REGIONI
from app.web import leggi_form, render, testo

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Autenticato = Annotated[Utente, Depends(utente_corrente)]
Titolare = Annotated[Utente, Depends(richiede_titolare)]
Form = Annotated[dict[str, Any], Depends(leggi_form)]
CSRF = [Depends(verifica_csrf)]

CAMPI_AZIENDA = (
    "ragione_sociale",
    "partita_iva",
    "codice_fiscale",
    "forma_giuridica",
    "dimensione_manuale",
    "tipo_soggetto",
    "pec",
    "email",
    "telefono",
    "codice_sdi",
    "stato_cliente",
    "note",
)
CAMPI_SEDE = ("indirizzo", "cap", "comune_testo", "provincia", "regione")
CAMPI_NUMERICI = (
    "dipendenti",
    "fatturato",
    "anno_dati",
    "segnalatore_id",
    "totale_attivo",
    "plafond_de_minimis_manuale",
    "abitanti",
)
# Altri campi dell'azienda (non numerici) salvati così come sono
CAMPI_PROFILO = ("data_costituzione", "frequenza_aggiornamenti")
CAMPI_EXTRA = ("stato_attivita", "ateco_codice", "ateco_descrizione", "da_registro")
_ATECO = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")


def _azienda(db: Session, azienda_id: int) -> Azienda:
    a = db.get(
        Azienda,
        azienda_id,
        options=[selectinload(Azienda.referenti), selectinload(Azienda.ateco)],
    )
    if a is None:
        raise HTTPException(404)
    return a


def _numero(valore: str | None, decimale: bool = False) -> Any:
    if not valore:
        return None
    if decimale:
        d = importo_italiano(valore)
        return d if d is not None else "non valido"
    try:
        return int(valore.replace(".", "").replace(" ", ""))
    except ValueError:
        return "non valido"


def _valida(db: Session, valori: dict[str, Any], azienda_id: int | None) -> dict[str, str]:
    errori: dict[str, str] = {}
    if not valori.get("ragione_sociale"):
        errori["ragione_sociale"] = "La ragione sociale è obbligatoria."
    piva = valori.get("partita_iva")
    if piva:
        if not partita_iva_valida(piva):
            errori["partita_iva"] = "Partita IVA non valida (11 cifre con cifra di controllo)."
        else:
            altra = db.scalar(select(Azienda).where(Azienda.partita_iva == piva))
            if altra is not None and altra.id != azienda_id:
                errori["partita_iva"] = f"Partita IVA già usata da «{altra.ragione_sociale}»."
    cf = valori.get("codice_fiscale")
    if cf and len(cf) not in (11, 16):
        errori["codice_fiscale"] = "Il codice fiscale ha 11 o 16 caratteri."
    sdi = valori.get("codice_sdi")
    if sdi and len(sdi) != 7:
        errori["codice_sdi"] = "Il codice SDI ha 7 caratteri."
    cap = valori.get("cap")
    if cap and not (cap.isdigit() and len(cap) == 5):
        errori["cap"] = "Il CAP ha 5 cifre."
    if valori.get("tipo_soggetto") not in TIPI_SOGGETTO:
        errori["tipo_soggetto"] = "Tipo di cliente non valido."
    if valori.get("stato_cliente") not in STATI_CLIENTE:
        errori["stato_cliente"] = "Stato non valido."
    if valori.get("dimensione_manuale") and valori["dimensione_manuale"] not in DIMENSIONI:
        errori["dimensione_manuale"] = "Dimensione non valida."
    if valori.get("regione") and valori["regione"] not in REGIONI:
        errori["regione"] = "Regione non valida."
    for c in CAMPI_NUMERICI:
        v = valori.get(c)
        if v == "non valido" or (v is not None and v < 0):
            errori[c] = "Numero non valido."
    if valori.get("ateco_codice") and not _ATECO.match(valori["ateco_codice"]):
        errori["ateco_codice"] = "Codice ATECO nel formato 62.01 o 62.01.00."
    soa, errore_soa = leggi_soa(valori.get("soa_testo") or "")
    if errore_soa:
        errori["soa_testo"] = errore_soa
    valori["soa_possedute"] = soa
    sedi_op, errore_sedi = leggi_sedi_operative(db, valori.get("sedi_operative_testo") or "")
    if errore_sedi:
        errori["sedi_operative_testo"] = errore_sedi
    valori["sedi_operative"] = sedi_op
    if any(r not in REGIONI for r in valori.get("regioni_interesse") or []):
        errori["regioni_interesse"] = "Regione non valida."
    if any(
        not c.isdigit() or c[:2] not in DIVISIONI_CPV for c in valori.get("cpv_interesse") or []
    ):
        errori["cpv_interesse"] = "Categoria non valida."
    if valori.get("data_costituzione") == "non valida" or (
        isinstance(valori.get("data_costituzione"), date)
        and valori["data_costituzione"] > date.today()
    ):
        errori["data_costituzione"] = "Data non valida."
    if valori.get("frequenza_aggiornamenti") not in FREQUENZE_AGGIORNAMENTO:
        errori["frequenza_aggiornamenti"] = "Frequenza non valida."
    sid = valori.get("segnalatore_id")
    if isinstance(sid, int) and db.get(Segnalatore, sid) is None:
        errori["segnalatore_id"] = "Segnalatore non trovato."
    return errori


def _valori(form: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {c: testo(form, c) for c in CAMPI_AZIENDA + CAMPI_SEDE + CAMPI_EXTRA}
    for c in ("partita_iva", "codice_fiscale", "codice_sdi", "provincia"):
        if v[c]:
            v[c] = v[c].replace(" ", "").upper()
    if v["partita_iva"] and v["partita_iva"].startswith("IT"):
        v["partita_iva"] = v["partita_iva"][2:]
    v["stato_cliente"] = v["stato_cliente"] or "attivo"
    v["tipo_soggetto"] = v["tipo_soggetto"] or tipo_dal_nome(
        v.get("ragione_sociale"), v.get("forma_giuridica")
    )
    v["abitanti"] = _numero(testo(form, "abitanti"))
    v["dipendenti"] = _numero(testo(form, "dipendenti"))
    v["anno_dati"] = _numero(testo(form, "anno_dati"))
    v["fatturato"] = _numero(testo(form, "fatturato"), decimale=True)
    v["segnalatore_id"] = _numero(testo(form, "segnalatore_id"))
    v["totale_attivo"] = _numero(testo(form, "totale_attivo"), decimale=True)
    v["plafond_de_minimis_manuale"] = _numero(
        testo(form, "plafond_de_minimis_manuale"), decimale=True
    )
    data_cost = testo(form, "data_costituzione")
    try:
        v["data_costituzione"] = date.fromisoformat(data_cost) if data_cost else None
    except ValueError:
        v["data_costituzione"] = "non valida"
    v["frequenza_aggiornamenti"] = testo(form, "frequenza_aggiornamenti") or "nessuno"
    v["tag_testo"] = testo(form, "tag_testo") or ""
    v["avvisi_attivi"] = form.get("avvisi_attivi") == "1"
    v["soa_testo"] = testo(form, "soa_testo") or ""
    v["sedi_operative_testo"] = testo(form, "sedi_operative_testo") or ""
    regioni = form.get("regioni_interesse") or []
    v["regioni_interesse"] = [regioni] if isinstance(regioni, str) else regioni
    cpv = form.get("cpv_interesse") or []
    v["cpv_interesse"] = [cpv] if isinstance(cpv, str) else cpv
    # codici più precisi delle divisioni (es. 79710000 vigilanza dentro la 79)
    from app.utils.cpv import codici_precisi

    v["cpv_precisi"] = testo(form, "cpv_precisi") or ""
    for codice in codici_precisi(v["cpv_precisi"]):
        if codice not in v["cpv_interesse"]:
            v["cpv_interesse"].append(codice)
    return v


def leggi_soa(testo_soa: str) -> tuple[list[dict[str, str]] | None, str | None]:
    """«OG6 III» / «OS22 II» (una per riga, o separate da virgola) → lista di attestazioni."""
    from app.appalti.scheda import CATEGORIE_SOA

    voci = []
    for pezzo in re.split(r"[\n,;]+", testo_soa):
        parti = pezzo.strip().upper().replace("BIS", "bis").split()
        if not parti:
            continue
        codice = parti[0].replace(" ", "")
        m = re.fullmatch(r"(O[GS])(\d{1,2})(?:-?([AB]))?", codice)
        if m:
            codice = f"{m.group(1)}{int(m.group(2))}" + (f"-{m.group(3)}" if m.group(3) else "")
        if codice not in CATEGORIE_SOA:
            return None, f"Categoria SOA sconosciuta: {parti[0]}."
        classifica = parti[1] if len(parti) > 1 else None
        if classifica and classifica not in CLASSIFICHE_SOA:
            return None, f"Classifica non valida per {codice}: usa I, II, III, III-bis … VIII."
        voci.append({"codice": codice, "classifica": classifica})
    return voci or None, None


MAX_SEDI_OPERATIVE = 30
_RIGA_SEDE = re.compile(r"^(.+?)\s*(?:\(\s*([A-Za-z]{2})\s*\))?$")


def leggi_sedi_operative(db: Session, testo_sedi: str) -> tuple[list[dict[str, Any]], str | None]:
    """«Prato (PO)» / «Bologna», una per riga → comuni riconosciuti con la tabella ISTAT
    (provincia e regione comprese). Senza tabella si tengono comune e sigla scritti."""
    from app.riferimenti.territorio import in_memoria

    t = in_memoria(db)
    sedi: list[dict[str, Any]] = []
    for riga in re.split(r"[\n;]+", testo_sedi):
        riga = " ".join(riga.split())
        if not riga:
            continue
        riga = re.sub(r"^\d{5}\s+", "", riga)  # CAP davanti al comune
        m = _RIGA_SEDE.match(riga)
        nome, sigla = m.group(1).strip(" ,-"), (m.group(2) or "").upper() or None
        # «Prato PO», «Prato, PO», «Prato - PO»: sigla senza parentesi
        senza = re.fullmatch(r"(.+?)[\s,\-]+([A-Za-z]{2})", nome)
        if (
            not sigla
            and senza
            and (t is None or (t.comune(nome) is None and t.comune(*senza.groups()) is not None))
        ):
            nome, sigla = senza.group(1), senza.group(2).upper()
        if t is not None:
            voce = t.comune(nome, sigla)
            if voce is None:
                ambiguo = not sigla and len(t.per_nome.get(norm_nome(nome), [])) > 1
                aiuto = " Aggiungi la provincia, es. «Calliano (TN)»." if ambiguo else ""
                giusto = t.comune(nome) if sigla else None
                if giusto is not None:
                    aiuto = f" {giusto.nome} è in provincia di {giusto.sigla}."
                return [], f"Comune non riconosciuto: «{riga}».{aiuto}"
            sede = {"comune_testo": voce.nome, "provincia": voce.sigla, "regione": voce.regione,
                    "codice_istat": voce.codice}  # fmt: skip
        else:
            sede = {"comune_testo": nome, "provincia": sigla, "regione": None, "codice_istat": None}
        chiave = (norm_nome(sede["comune_testo"]), sede["provincia"])
        if chiave not in {(norm_nome(s["comune_testo"]), s["provincia"]) for s in sedi}:
            sedi.append(sede)
    if len(sedi) > MAX_SEDI_OPERATIVE:
        return [], f"Al massimo {MAX_SEDI_OPERATIVE} sedi operative."
    return sedi, None


def _testo_sedi_operative(a: Azienda) -> str:
    return "\n".join(
        f"{s.comune_testo} ({s.provincia})" if s.provincia else s.comune_testo
        for s in a.sedi
        if s.tipo == "operativa" and s.comune_testo
    )


def _applica_sedi_operative(azienda: Azienda, nuove: list[dict[str, Any]]) -> bool:
    """Allinea le sedi operative all'elenco: quelle che restano tengono indirizzo e CAP."""
    attuali = [s for s in azienda.sedi if s.tipo == "operativa"]
    chiave = lambda c, p: (norm_nome(c or ""), (p or "").upper() or None)  # noqa: E731
    per_chiave = {chiave(s.comune_testo, s.provincia): s for s in attuali}
    tenute = set()
    cambiate = False
    for d in nuove:
        s = per_chiave.get(chiave(d["comune_testo"], d["provincia"]))
        if s is None:  # salvata senza provincia (tabella ISTAT non ancora caricata)
            s = per_chiave.get(chiave(d["comune_testo"], None))
        if s is None:
            s = Sede(tipo="operativa")
            azienda.sedi.append(s)
            cambiate = True
        for c, valore in d.items():
            if valore is not None and getattr(s, c) != valore:
                setattr(s, c, valore)
        tenute.add(id(s))
    for s in attuali:
        if id(s) not in tenute:
            azienda.sedi.remove(s)
            cambiate = True
    return cambiate


def _valori_da_azienda(a: Azienda) -> dict[str, Any]:
    sede = a.sede_legale
    v: dict[str, Any] = {c: getattr(a, c) for c in CAMPI_AZIENDA + CAMPI_NUMERICI + CAMPI_PROFILO}
    v["tag_testo"] = ", ".join(t.nome for t in a.tag)
    v.update({c: getattr(sede, c) if sede else None for c in CAMPI_SEDE})
    v["stato_attivita"] = a.stato_attivita
    v["avvisi_attivi"] = a.avvisi_attivi
    v["soa_testo"] = "\n".join(
        f"{s['codice']} {s.get('classifica') or ''}".strip() for s in a.soa_possedute or []
    )
    v["regioni_interesse"] = a.regioni_interesse or []
    v["cpv_interesse"] = a.cpv_interesse or []
    v["cpv_precisi"] = " ".join(c for c in a.cpv_interesse or [] if len(c) > 2)
    v["sedi_operative_testo"] = _testo_sedi_operative(a)
    if a.ateco_primario:
        v["ateco_codice"] = a.ateco_primario.codice
        v["ateco_descrizione"] = a.ateco_primario.descrizione
    return v


def _riconosci_comune(db: Session, azienda: Azienda) -> None:
    """Codice ISTAT del comune della sede (e provincia/regione se mancano)."""
    from app.riferimenti.territorio import completa_sede, in_memoria

    if azienda.sede_legale is not None:
        completa_sede(in_memoria(db), azienda.sede_legale)


def _applica_tag(db: Session, azienda: Azienda, testo_tag: str) -> None:
    """Tag separati da virgola; quelli nuovi si creano."""
    nomi = []
    for n in testo_tag.split(","):
        n = " ".join(n.split())[:60]
        if n and n.lower() not in {x.lower() for x in nomi}:
            nomi.append(n)
    tag = []
    for n in nomi:
        t = db.scalar(select(Tag).where(func.lower(Tag.nome) == n.lower()))
        if t is None:
            t = Tag(nome=n)
            db.add(t)
        tag.append(t)
    azienda.tag = tag


def _form(
    request: Request,
    db: Session,
    azienda: Azienda | None,
    valori: dict[str, Any],
    errori: dict[str, str],
    messaggio: tuple[str, str] | None = None,
) -> Response:
    return render(
        request,
        "clienti/form.html",
        {
            "azienda": azienda,
            "v": valori,
            "errori": errori,
            "messaggio": messaggio,
            "regioni": REGIONI,
            "dimensioni": DIMENSIONI,
            "stati": STATI_CLIENTE,
            "frequenze": FREQUENZE_AGGIORNAMENTO,
            "tag_esistenti": list(db.scalars(select(Tag.nome).order_by(Tag.nome))),
            "registro_disponibile": integrazioni.utilizzabile(db, "registro_imprese"),
            "classifiche": list(CLASSIFICHE_SOA),
            "divisioni_cpv": DIVISIONI_CPV,
            "segnalatori": list(
                db.scalars(select(Segnalatore).where(Segnalatore.attivo).order_by(Segnalatore.nome))
            ),
        },
        400 if errori else 200,
    )


def _applica(azienda: Azienda, v: dict[str, Any]) -> dict[str, Any]:
    differenze: dict[str, Any] = {}
    for c in CAMPI_AZIENDA + CAMPI_NUMERICI + CAMPI_PROFILO:
        if getattr(azienda, c) != v[c]:
            differenze[c] = str(v[c]) if v[c] is not None else None
            setattr(azienda, c, v[c])
    sede = azienda.sede_legale
    if sede is None and any(v[c] for c in CAMPI_SEDE):
        sede = Sede(tipo="legale")
        azienda.sedi.append(sede)
    if sede is not None:
        for c in CAMPI_SEDE:
            if getattr(sede, c) != v[c]:
                differenze[f"sede_{c}"] = v[c]
                setattr(sede, c, v[c])
    if "sedi_operative" in v and _applica_sedi_operative(azienda, v["sedi_operative"]):
        differenze["sedi_operative"] = v.get("sedi_operative_testo") or None
    profilo = {
        "avvisi_attivi": bool(v.get("avvisi_attivi")),
        "soa_possedute": v.get("soa_possedute") or None,
        "regioni_interesse": sorted(v.get("regioni_interesse") or []) or None,
        "cpv_interesse": sorted(v.get("cpv_interesse") or []) or None,
    }
    for campo, valore in profilo.items():
        if getattr(azienda, campo) != valore:
            setattr(azienda, campo, valore)
            differenze[campo] = valore
    if v.get("stato_attivita") != azienda.stato_attivita:
        azienda.stato_attivita = v.get("stato_attivita")
    if v.get("da_registro"):
        azienda.registro_imprese_il = datetime.now(UTC)
        differenze["registro_imprese"] = "dati compilati dal Registro Imprese"
    # ATECO primario (versione 2007: quella usata dal Registro Imprese tramite Openapi)
    codice = v.get("ateco_codice")
    attuale = azienda.ateco_primario
    if codice and (attuale is None or attuale.codice != codice):
        for a in azienda.ateco:
            a.primario = False
        esistente = next((a for a in azienda.ateco if a.codice == codice), None)
        if esistente:
            esistente.primario = True
            esistente.descrizione = v.get("ateco_descrizione") or esistente.descrizione
        else:
            azienda.ateco.append(
                AziendaAteco(
                    codice=codice,
                    descrizione=v.get("ateco_descrizione"),
                    versione="2007",
                    primario=True,
                )
            )
        differenze["ateco_primario"] = codice
    elif codice and attuale is not None and v.get("ateco_descrizione"):
        attuale.descrizione = v["ateco_descrizione"]
    elif not codice and attuale is not None:
        attuale.primario = False
        differenze["ateco_primario"] = None
    return differenze


@router.get("/clienti")
def lista(request: Request, db: Db, _: Autenticato) -> Response:
    q = (request.query_params.get("q") or "").strip()
    stato = request.query_params.get("stato") or ""
    query = select(Azienda).options(selectinload(Azienda.sedi)).order_by(Azienda.ragione_sociale)
    testo = condizione(
        q, Azienda.ragione_sociale, Azienda.partita_iva, Azienda.codice_fiscale, Azienda.email
    )
    if testo is not None:
        query = query.where(testo)
    if stato in STATI_CLIENTE:
        query = query.where(Azienda.stato_cliente == stato)
    p = request.query_params
    regione = p.get("regione") or ""
    if regione in REGIONI:
        query = query.where(Azienda.sedi.any(Sede.regione == regione))
    ateco = (p.get("ateco") or "").strip()
    if ateco:
        query = query.where(Azienda.ateco.any(AziendaAteco.codice.startswith(ateco)))
    tag = p.get("tag") or ""
    if tag:
        query = query.where(Azienda.tag.any(Tag.nome == tag))
    aziende = list(db.scalars(query.options(selectinload(Azienda.tag))))
    dimensione = p.get("dimensione") or ""
    if dimensione in DIMENSIONI:
        from app.utils.impresa import dimensione_effettiva

        aziende = [a for a in aziende if dimensione_effettiva(a)[0] == dimensione]
    conteggi = dict(
        db.execute(select(Pratica.azienda_id, func.count()).group_by(Pratica.azienda_id)).all()
    )
    return render(
        request,
        "clienti/lista.html",
        {
            "aziende": aziende,
            "conteggi": conteggi,
            "filtri": {
                "q": q,
                "stato": stato,
                "regione": regione,
                "ateco": ateco,
                "tag": tag,
                "dimensione": dimensione,
            },
            "stati": STATI_CLIENTE,
            "regioni": REGIONI,
            "dimensioni": DIMENSIONI,
            "tag_esistenti": list(db.scalars(select(Tag.nome).order_by(Tag.nome))),
        },
    )


@router.get("/clienti/nuovo")
def nuovo(request: Request, db: Db, _: Autenticato) -> Response:
    return _form(request, db, None, {"stato_cliente": "attivo"}, {})


@router.post("/clienti/compila", dependencies=CSRF)
def compila_da_registro(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    """Pulsante "Compila dal Registro Imprese": cerca per partita IVA (o codice fiscale) e
    riempie il form con i dati trovati. Niente viene salvato finché l'utente non preme Salva."""
    azienda_id = _numero(testo(form, "azienda_id"))
    azienda = _azienda(db, azienda_id) if isinstance(azienda_id, int) else None
    v = _valori(form)
    identificativo = v.get("partita_iva") or v.get("codice_fiscale") or ""
    if not integrazioni.utilizzabile(db, "registro_imprese"):
        return _form(
            request,
            db,
            azienda,
            v,
            {},
            ("errore", "Il Registro Imprese non è collegato o è disattivato nelle impostazioni."),
        )
    try:
        dati = cerca(integrazioni.leggi_valori(db, "registro_imprese"), identificativo)
    except ErroreRegistro as e:
        return _form(request, db, azienda, v, {}, ("errore", str(e)))
    except ErroreCifratura:
        return _form(
            request,
            db,
            azienda,
            v,
            {},
            ("errore", "Il token salvato non si può decifrare: reinseriscilo nelle impostazioni."),
        )
    finally:
        audit.registra(
            db,
            utente,
            "registro_imprese",
            identificativo[:60] or None,
            "invia",
            {"ricerca": identificativo},
        )
        db.commit()
    for campo, valore in dati.campi_compilati().items():
        if campo in v or campo in CAMPI_NUMERICI:
            v[campo] = valore
    v["da_registro"] = "1"
    testo_esito = "Dati compilati dal Registro Imprese: controllali e premi Salva."
    if dati.avvisi:
        testo_esito += " " + " ".join(dati.avvisi)
    return _form(request, db, azienda, v, {}, ("attenzione" if dati.avvisi else "ok", testo_esito))


@router.post("/clienti/nuovo", dependencies=CSRF)
def crea(request: Request, db: Db, utente: Autenticato, form: Form) -> Response:
    v = _valori(form)
    errori = _valida(db, v, None)
    if errori:
        return _form(request, db, None, v, errori)
    azienda = Azienda(ragione_sociale=v["ragione_sociale"])
    _applica(azienda, v)
    _riconosci_comune(db, azienda)
    db.add(azienda)
    _applica_tag(db, azienda, v["tag_testo"])
    db.flush()
    referente = testo(form, "referente_nome")
    if referente:
        azienda.referenti.append(
            Referente(
                nome=referente,
                cognome=testo(form, "referente_cognome"),
                email=testo(form, "referente_email"),
                telefono=testo(form, "referente_telefono"),
                principale=True,
            )
        )
    audit.registra(
        db, utente, "azienda", azienda.id, "crea", {"ragione_sociale": v["ragione_sociale"]}
    )
    db.commit()
    return RedirectResponse(f"/clienti/{azienda.id}", status_code=303)


@router.get("/clienti/{azienda_id}")
def scheda(request: Request, azienda_id: int, db: Db, _: Autenticato) -> Response:
    azienda = _azienda(db, azienda_id)
    pratiche = list(
        db.scalars(
            select(Pratica)
            .where(Pratica.azienda_id == azienda_id)
            .options(selectinload(Pratica.bando), selectinload(Pratica.fasi))
            .order_by(Pratica.creato_il.desc())
        )
    )
    from app.models.opportunita import Opportunita

    opportunita = list(
        db.scalars(
            select(Opportunita)
            .where(
                Opportunita.azienda_id == azienda_id,
                Opportunita.stato.in_(("nuova", "vista", "interessante")),
            )
            .options(selectinload(Opportunita.bando), selectinload(Opportunita.appalto))
            .order_by(Opportunita.punteggio.desc())
            .limit(8)
        )
    )
    from app.clienti.crm import dati_scheda

    documenti = list(
        db.scalars(
            select(Allegato)
            .where(Allegato.azienda_id == azienda_id)
            .options(selectinload(Allegato.utente))
            .order_by(Allegato.creato_il.desc())
        )
    )
    return render(
        request,
        "clienti/scheda.html",
        {
            **dati_scheda(db, azienda),
            "documenti": documenti,
            "codici_pratica": {p.id: p.codice for p in pratiche},
            "opportunita": opportunita,
            "azienda": azienda,
            "pratiche": pratiche,
            "etichette_stato": ETICHETTE_STATO_PRATICA,
            "registro_disponibile": integrazioni.utilizzabile(db, "registro_imprese"),
        },
    )


@router.post("/clienti/{azienda_id}/documenti", dependencies=CSRF)
def carica_documento(
    azienda_id: int,
    db: Db,
    utente: Autenticato,
    file: Annotated[UploadFile, File()],
) -> Response:
    """Documento dell'azienda (visura, bilancio, DURC...): sulla scheda, fuori dalle pratiche."""
    from app.pratiche import allegati as archivio

    a = _azienda(db, azienda_id)
    nome = (file.filename or "documento").replace("\\", "/").rsplit("/", 1)[-1][:200]
    try:
        archivio.nuovo_allegato(db, file.file, nome, azienda_id=a.id, caricato_da=utente.id)
    except archivio.ErroreAllegato as e:
        return RedirectResponse(
            f"/clienti/{a.id}?{urlencode({'msg_doc': str(e)})}#documenti", status_code=303
        )
    audit.registra(db, utente, "azienda", a.id, "modifica", {"documento": nome})
    db.commit()
    return RedirectResponse(f"/clienti/{a.id}#documenti", status_code=303)


@router.get("/clienti/{azienda_id}/modifica")
def modifica(request: Request, azienda_id: int, db: Db, _: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    return _form(request, db, a, _valori_da_azienda(a), {})


@router.post("/clienti/{azienda_id}/modifica", dependencies=CSRF)
def salva(request: Request, azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    v = _valori(form)
    errori = _valida(db, v, a.id)
    if errori:
        return _form(request, db, a, v, errori)
    differenze = _applica(a, v)
    _riconosci_comune(db, a)
    prima = [t.nome for t in a.tag]
    _applica_tag(db, a, v["tag_testo"])
    if [t.nome for t in a.tag] != prima:
        differenze["tag"] = [t.nome for t in a.tag]
    if differenze:
        audit.registra(db, utente, "azienda", a.id, "modifica", differenze)
    db.commit()
    return RedirectResponse(f"/clienti/{a.id}", status_code=303)


@router.post("/clienti/{azienda_id}/elimina", dependencies=CSRF)
def elimina(request: Request, azienda_id: int, db: Db, utente: Titolare) -> Response:
    a = _azienda(db, azienda_id)
    if db.scalar(select(func.count()).select_from(Pratica).where(Pratica.azienda_id == a.id)):
        return render(
            request,
            "errore.html",
            {"messaggio": "Il cliente ha delle pratiche: elimina prima quelle."},
            400,
        )
    audit.registra(db, utente, "azienda", a.id, "elimina", {"ragione_sociale": a.ragione_sociale})
    from app.cestino import servizio as cestino

    e = cestino.sposta(db, utente, a, "cliente", a.ragione_sociale, f"/clienti/{a.id}")
    db.delete(a)
    db.flush()
    from app.squadra.note import pulisci_orfane

    pulisci_orfane(db)  # note delle sue opportunità
    db.commit()
    return RedirectResponse(f"/clienti?cestino={e.id}", status_code=303)


@router.post("/clienti/{azienda_id}/referenti", dependencies=CSRF)
def aggiungi_referente(azienda_id: int, db: Db, utente: Autenticato, form: Form) -> Response:
    a = _azienda(db, azienda_id)
    nome = testo(form, "nome")
    if nome:
        principale = not a.referenti or form.get("principale") == "1"
        if principale:
            for r in a.referenti:
                r.principale = False
        ref = Referente(
            nome=nome,
            cognome=testo(form, "cognome"),
            ruolo=testo(form, "ruolo"),
            email=testo(form, "email"),
            telefono=testo(form, "telefono"),
            principale=principale,
        )
        a.referenti.append(ref)
        audit.registra(db, utente, "azienda", a.id, "modifica", {"referente_aggiunto": nome})
        db.commit()
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)


@router.post("/clienti/{azienda_id}/referenti/{referente_id}/principale", dependencies=CSRF)
def referente_principale(azienda_id: int, referente_id: int, db: Db, _: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    for r in a.referenti:
        r.principale = r.id == referente_id
    db.commit()
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)


@router.post("/clienti/{azienda_id}/referenti/{referente_id}/elimina", dependencies=CSRF)
def elimina_referente(azienda_id: int, referente_id: int, db: Db, utente: Autenticato) -> Response:
    a = _azienda(db, azienda_id)
    ref = next((r for r in a.referenti if r.id == referente_id), None)
    if ref is not None:
        from app.cestino import servizio as cestino

        link = f"/clienti/{a.id}#referenti"
        e = cestino.sposta(db, utente, ref, "referente", ref.nome_completo, link)
        a.referenti.remove(ref)
        audit.registra(
            db, utente, "azienda", a.id, "modifica", {"referente_eliminato": ref.nome_completo}
        )
        db.commit()
        return RedirectResponse(f"/clienti/{a.id}?cestino={e.id}#referenti", status_code=303)
    return RedirectResponse(f"/clienti/{a.id}#referenti", status_code=303)
