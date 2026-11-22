"""Impostazioni (attiva/disattiva, guida, Registro Imprese, email di prova), compilazione del
cliente, invio email dalla pratica, allegati, segnalatori, prefattibilità, statistiche,
appalti, promemoria. Nessuna chiamata di rete vera: i servizi esterni sono sostituiti."""

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.integrazioni import servizio as integrazioni
from app.integrazioni.registro_imprese import DatiImpresa
from app.models import (
    Allegato,
    Appalto,
    Azienda,
    Integrazione,
    ModelloPratica,
    NotificaEmail,
    Pratica,
    Prefattibilita,
    Segnalatore,
)
from tests.integration.conftest import csrf
from tests.integration.test_pratiche import _bando, _cliente, _modelli_esempio

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _integrazioni_pulite(db):
    """Le integrazioni sono righe uniche per codice: ogni test parte da zero."""
    db.query(Integrazione).delete()
    db.commit()
    yield
    db.query(Integrazione).delete()
    db.commit()


def _configura_smtp(client):
    token = csrf(client, "/impostazioni/integrazioni/smtp")
    r = client.post(
        "/impostazioni/integrazioni/smtp",
        data={
            "csrf_token": token,
            "host": "smtp.esempio.it",
            "porta": "587",
            "sicurezza": "starttls",
            "utente": "studio@esempio.it",
            "password": "segreta",
            "mittente": "studio@esempio.it",
        },
    )
    assert r.status_code == 303


def _configura_registro(client, attiva=True):
    token = csrf(client, "/impostazioni/integrazioni/registro_imprese")
    client.post(
        "/impostazioni/integrazioni/registro_imprese",
        data={
            "csrf_token": token,
            "token": "tok-prova",
            "ambiente": "sandbox",
            "livello": "advanced",
        },
    )
    if attiva:
        client.post(
            "/impostazioni/integrazioni/registro_imprese/attiva",
            data={"csrf_token": token, "attiva": "1"},
        )


# --- Impostazioni ------------------------------------------------------------------------


def test_impostazioni_guida_e_interruttore(client, crea_utente, accedi, db):
    accedi(crea_utente())
    elenco = client.get("/impostazioni/integrazioni").text
    for nome in ("OpenAI", "Email (SMTP)", "WhatsApp Business", "Registro Imprese (Openapi)"):
        assert f"Come collegare {nome}" in elenco
    pagina = client.get("/impostazioni/integrazioni/registro_imprese").text
    assert "https://console.openapi.com/it/oauth" in pagina
    assert "GET:company.openapi.com/IT-advanced" in pagina
    assert "Disattivata" in pagina  # a pagamento: parte spenta

    _configura_registro(client, attiva=False)
    assert integrazioni.stato(db, "registro_imprese").configurata
    assert not integrazioni.utilizzabile(db, "registro_imprese")
    token = csrf(client, "/impostazioni/integrazioni")
    r = client.post(
        "/impostazioni/integrazioni/registro_imprese/attiva",
        data={"csrf_token": token, "attiva": "1", "torna": "elenco"},
    )
    assert r.headers["location"] == "/impostazioni/integrazioni"
    db.expire_all()
    assert integrazioni.utilizzabile(db, "registro_imprese")

    # disattivando l'email le comunicazioni non partono, ma le impostazioni restano
    _configura_smtp(client)
    client.post("/impostazioni/integrazioni/smtp/attiva", data={"csrf_token": token, "attiva": "0"})
    db.expire_all()
    s = integrazioni.stato(db, "smtp")
    assert s.configurata and not s.attiva


def test_verifica_registro_imprese(client, crea_utente, accedi, monkeypatch):
    accedi(crea_utente())
    _configura_registro(client)
    monkeypatch.setattr(
        "app.integrazioni.registro_imprese.cerca",
        lambda valori, piva, client=None: DatiImpresa(ragione_sociale="OPENAPI S.P.A."),
    )
    token = csrf(client, "/impostazioni/integrazioni/registro_imprese")
    r = client.post(
        "/impostazioni/integrazioni/registro_imprese/verifica", data={"csrf_token": token}
    )
    assert "Connessione riuscita (sandbox)" in r.text and "OPENAPI S.P.A." in r.text


def test_email_di_prova(client, crea_utente, accedi, db, monkeypatch):
    utente = crea_utente()
    accedi(utente)
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append(a),
    )
    token = csrf(client, "/impostazioni/integrazioni/smtp")
    r = client.post("/impostazioni/integrazioni/smtp/prova", data={"csrf_token": token})
    assert r.status_code == 200 and "Email di prova inviata" in r.text
    assert inviate == [utente.email]
    riga = db.scalar(
        select(NotificaEmail).where(NotificaEmail.tipo == "prova").order_by(NotificaEmail.id.desc())
    )
    assert riga.esito == "ok" and riga.destinatario == utente.email


# --- Cliente dal Registro Imprese --------------------------------------------------------


def test_compila_cliente_dal_registro(client, crea_utente, accedi, db, monkeypatch):
    accedi(crea_utente())
    token = csrf(client, "/clienti/nuovo")
    assert "Compila dal Registro Imprese" not in client.get("/clienti/nuovo").text
    _configura_registro(client)
    assert "Compila dal Registro Imprese" in client.get("/clienti/nuovo").text

    from tests.integration.test_pratiche import _piva

    piva = _piva(db)
    monkeypatch.setattr(
        "app.clienti.router.cerca",
        lambda valori, ident: DatiImpresa(
            ragione_sociale="ROSSI SRL",
            partita_iva=piva,
            pec="rossi@pec.it",
            codice_sdi="ABCDEF1",
            comune_testo="Firenze",
            provincia="FI",
            regione="Toscana",
            ateco_codice="62.01",
            ateco_descrizione="Produzione di software",
            dipendenti=12,
            fatturato=Decimal("900000"),
            anno_dati=2025,
            stato_attivita="ATTIVA",
        ),
    )
    r = client.post("/clienti/compila", data={"csrf_token": token, "partita_iva": piva})
    assert r.status_code == 200
    assert "Dati compilati dal Registro Imprese" in r.text and 'value="ROSSI SRL"' in r.text
    # Salvataggio con i dati compilati (il form li ripropone)
    r = client.post(
        "/clienti/nuovo",
        data={
            "csrf_token": token,
            "ragione_sociale": "ROSSI SRL",
            "partita_iva": piva,
            "regione": "Toscana",
            "ateco_codice": "62.01",
            "ateco_descrizione": "Produzione di software",
            "dipendenti": "12",
            "fatturato": "900.000",
            "anno_dati": "2025",
            "stato_attivita": "ATTIVA",
            "da_registro": "1",
        },
    )
    assert r.status_code == 303
    a = db.scalar(select(Azienda).where(Azienda.partita_iva == piva))
    assert a.ateco_primario.codice == "62.01" and a.dipendenti == 12
    assert a.fatturato == Decimal("900000") and a.registro_imprese_il is not None


# --- Pratica: invio email e allegati -----------------------------------------------------


def _pratica_con_modello(client, db, utente) -> Pratica:
    _modelli_esempio(client)
    azienda = _cliente(client, db)
    modello = db.scalar(
        select(ModelloPratica).where(ModelloPratica.nome.like("Contributo a fondo perduto%"))
    )
    token = csrf(client, "/pratiche/nuova")
    r = client.post(
        "/pratiche/nuova",
        data={
            "csrf_token": token,
            "azienda_id": str(azienda.id),
            "bando_id": str(_bando(db).id),
            "modello_id": str(modello.id),
            "responsabile_id": str(utente.id),
        },
    )
    return db.get(Pratica, int(r.headers["location"].rsplit("/", 1)[1]))


def test_invio_email_dalla_pratica(client, crea_utente, accedi, db, monkeypatch):
    utente = crea_utente()
    accedi(utente)
    pratica = _pratica_con_modello(client, db, utente)
    op = next(o for f in pratica.fasi for o in f.operazioni if o.tipo == "comunicazione")
    token = csrf(client, "/pratiche")
    # senza SMTP il pulsante non c'è
    assert "Invia email a" not in client.get(f"/pratiche/{pratica.id}?fase=4").text
    _configura_smtp(client)
    assert "Invia email a maria@esempio.it" in client.get(f"/pratiche/{pratica.id}?fase=4").text
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)),
    )
    r = client.post(f"/pratiche/{pratica.id}/operazioni/{op.id}/invia", data={"csrf_token": token})
    assert r.status_code == 303 and "esito=ok" in r.headers["location"]
    a, oggetto, testo = inviate[0]
    assert a == "maria@esempio.it" and "Gentile Maria Rossi" in testo
    db.expire_all()
    op = db.get(type(op), op.id)
    assert op.stato == "fatta"
    assert db.scalar(
        select(NotificaEmail).where(
            NotificaEmail.pratica_id == pratica.id, NotificaEmail.tipo == "comunicazione_pratica"
        )
    )


def test_allegati_della_pratica(client, crea_utente, accedi, db, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cartella_allegati", str(tmp_path))
    utente = crea_utente()
    accedi(utente)
    pratica = _pratica_con_modello(client, db, utente)
    doc = next(d for f in pratica.fasi for o in f.operazioni for d in o.documenti)
    token = csrf(client, f"/pratiche/{pratica.id}")
    r = client.post(
        f"/pratiche/{pratica.id}/allegati",
        data={"csrf_token": token, "documento_id": str(doc.id)},
        files={"file": ("visura.pdf", b"%PDF-1.4 visura", "application/pdf")},
    )
    assert r.status_code == 303 and "esito=ok" in r.headers["location"]
    db.expire_all()
    allegato = db.scalar(select(Allegato).where(Allegato.pratica_id == pratica.id))
    assert allegato.nome_originale == "visura.pdf"
    assert db.get(type(doc), doc.id).stato == "ricevuto"
    scaricato = client.get(f"/allegati/{allegato.id}")
    assert scaricato.content == b"%PDF-1.4 visura"
    assert "attachment" in scaricato.headers["content-disposition"]
    # tipo non ammesso
    r = client.post(
        f"/pratiche/{pratica.id}/allegati",
        data={"csrf_token": token},
        files={"file": ("script.exe", b"MZ", "application/octet-stream")},
    )
    assert "esito=errore" in r.headers["location"]
    client.post(f"/allegati/{allegato.id}/elimina", data={"csrf_token": token})
    # il file resta finché l'allegato è nel cestino; sparisce quando lo si svuota
    assert list(tmp_path.rglob("*.pdf"))
    from app.cestino import servizio as cestino
    from app.models import ElementoCestino

    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "allegato")
                  .order_by(ElementoCestino.id.desc()))  # fmt: skip
    cestino.elimina_definitivamente(db, e)
    db.commit()
    assert not list(tmp_path.rglob("*.pdf"))


# --- Segnalatori, prefattibilità, statistiche --------------------------------------------


def test_segnalatori_prefattibilita_statistiche(client, crea_utente, accedi, db):
    utente = crea_utente()
    accedi(utente)
    token = csrf(client, "/segnalatori")
    r = client.post(
        "/segnalatori",
        data={"csrf_token": token, "nome": "Luca Bianchi", "provvigione_percentuale": "10"},
    )
    assert r.status_code == 303
    segn = db.scalar(
        select(Segnalatore)
        .where(Segnalatore.nome == "Luca Bianchi")
        .order_by(Segnalatore.id.desc())
    )
    azienda = _cliente(client, db, segnalatore_id=str(segn.id))
    assert azienda.segnalatore_id == segn.id

    bando = _bando(db)
    r = client.post(
        "/prefattibilita/nuova",
        data={"csrf_token": token, "azienda_id": str(azienda.id), "bando_id": str(bando.id)},
    )
    assert r.status_code == 303
    pf = db.get(Prefattibilita, int(r.headers["location"].rsplit("/", 1)[1]))
    assert pf.voci and pf.esito == "da_valutare"
    client.post(
        f"/prefattibilita/{pf.id}",
        data={
            "csrf_token": token,
            "esito": "fattibile",
            "contributo_stimato": "20.000",
            "nuovo_requisito": "DURC regolare",
        },
    )
    db.expire_all()
    pf = db.get(Prefattibilita, pf.id)
    assert pf.esito == "fattibile" and pf.voci[-1]["requisito"] == "DURC regolare"
    pagina = client.get(f"/prefattibilita/{pf.id}").text
    assert f"prefattibilita_id={pf.id}" in pagina  # pulsante "Apri la pratica"

    r = client.post(
        "/pratiche/nuova",
        data={
            "csrf_token": token,
            "azienda_id": str(azienda.id),
            "bando_id": str(bando.id),
            "prefattibilita_id": str(pf.id),
        },
    )
    pratica = db.get(Pratica, int(r.headers["location"].rsplit("/", 1)[1]))
    db.expire_all()
    assert db.get(Prefattibilita, pf.id).pratica_id == pratica.id
    assert pratica.importo_richiesto == Decimal("20000")

    client.post(
        f"/pratiche/{pratica.id}/dati",
        data={"csrf_token": token, "stato": "ammessa", "importo_concesso": "18.000"},
    )
    r = client.get(f"/segnalatori/{segn.id}")
    assert "€ 18.000,00" in r.text and "€ 1.800,00" in r.text  # provvigione 10%
    r = client.get("/statistiche")
    assert r.status_code == 200 and "Tasso di successo" in r.text and "Luca Bianchi" in r.text


# --- Appalti -----------------------------------------------------------------------------


def test_pagina_appalti(client, crea_utente, accedi, db):
    accedi(crea_utente())
    from app.appalti.ted import interpreta

    campione = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "ted_campione.json").read_text(encoding="utf-8")
    )
    dati = interpreta(campione[0])
    dati["id_esterno"] = f"prova-{uuid.uuid4().hex[:8]}"
    dati["scadenza"] = date.today() + timedelta(days=10)
    dati["titolo"] = "Servizi di prova per la ricerca appalti"
    db.add(Appalto(fonte="ted", **dati))
    db.commit()
    r = client.get("/appalti?regione=Lazio&q=ricerca+appalti")
    assert r.status_code == 200 and "Servizi di prova per la ricerca appalti" in r.text
    assert "tra 10 giorni" in r.text
    assert "Servizi di prova" not in client.get("/appalti?regione=Toscana&q=ricerca+appalti").text
    assert "Banca Dati Nazionale dei Contratti Pubblici" in r.text


# --- Promemoria --------------------------------------------------------------------------


def test_promemoria_scadenze(client, crea_utente, accedi, db, monkeypatch):
    from app.jobs import attivita

    utente = crea_utente()
    accedi(utente)
    _pratica_con_modello(client, db, utente)
    assert "non configurata" in attivita.promemoria_scadenze(date.today() + timedelta(days=2))
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, testo)),
    )
    esito = attivita.promemoria_scadenze(date.today() + timedelta(days=2))
    mie = [t for a, t in inviate if a == utente.email]
    assert mie and "Dati per la verifica" in mie[0] and "Promemoria inviati" in esito
    # una sola volta al giorno
    inviate.clear()
    attivita.promemoria_scadenze(date.today() + timedelta(days=2))
    assert not [a for a, _ in inviate if a == utente.email]


def test_import_anac_scheda_e_filtro_soa(client, crea_utente, accedi, db):
    import io
    import zipfile

    from tests.unit.test_appalti_fonti import INTESTAZIONE_CIG

    accedi(crea_utente())
    cig = f"Z{uuid.uuid4().hex[:9].upper()}"
    cig_ted = f"Y{uuid.uuid4().hex[:9].upper()}"
    scadenza = (date.today() + timedelta(days=14)).isoformat()
    righe_cig = "\n".join(
        [
            INTESTAZIONE_CIG,
            f"{cig};1;Razionalizzazione del sistema depurativo;2882628.25;1;;2882628.25;LAVORI;"
            f"ATTIVO;ORDINARIO;069046;Chieti;2026-11-04;{scadenza};1;PROCEDURA APERTA;"
            "SASI SPA;SEZIONE REGIONALE ABRUZZO;45252100;Impianti di depurazione",
            f"{cig_ted};1;Gara già su TED;100;1;;100;LAVORI;ATTIVO;ORDINARIO;;;"
            f"2026-11-04;{scadenza};1;PROCEDURA APERTA;ENTE;;;",
            "X000000001;1;Gara scaduta;1;1;;1;LAVORI;;;;;2025-01-01;2025-02-01;1;;ENTE;;;",
        ]
    )
    pacchetto = io.BytesIO()
    with zipfile.ZipFile(pacchetto, "w") as z:
        z.writestr("cig_csv.csv", righe_cig)
    categorie = (
        "cig;id_categoria;descrizione;cod_tipo_categoria;descrizione_tipo_categoria\n"
        f"{cig};OG6;ACQUEDOTTI;P;PREVALENTE\n{cig};OS22;POTABILIZZAZIONE;S;SCORPORABILE\n"
        f"{cig_ted};OG1;EDIFICI;P;PREVALENTE\n"
    )
    # una gara TED con lo stesso CIG riceve le categorie senza essere duplicata
    db.add(
        Appalto(
            fonte="ted",
            id_esterno=f"prova-{uuid.uuid4().hex[:8]}",
            titolo="Gara TED con CIG",
            cig=cig_ted,
            scadenza=date.today() + timedelta(days=14),
            raw={},
        )
    )
    db.commit()
    token = csrf(client, "/appalti/importa")
    assert "Come scaricare i file da ANAC" in client.get("/appalti/importa").text
    r = client.post(
        "/appalti/importa",
        data={"csrf_token": token},
        files=[
            ("file", ("categorie-opera.csv", categorie.encode(), "text/csv")),
            ("file", ("cig_2026.zip", pacchetto.getvalue(), "application/zip")),
        ],
    )
    assert r.status_code == 303 and "errore" not in r.headers["location"], r.headers["location"]
    pagina = client.get(r.headers["location"]).text
    assert "1 gare aperte importate" in pagina and "1 aggiornate" in pagina
    db.expire_all()
    a = db.scalar(select(Appalto).where(Appalto.cig == cig))
    assert a.fonte == "anac" and a.regioni == ["Abruzzo"] and a.province == ["Chieti"]
    assert a.importo_stimato == Decimal("2882628.25") and a.natura == ["lavori"]
    assert [(c["codice"], c["tipo"]) for c in a.categorie_soa] == [
        ("OG6", "prevalente"),
        ("OS22", "scorporabile"),
    ]
    ted_a = db.scalar(select(Appalto).where(Appalto.cig == cig_ted))
    assert ted_a.fonte == "ted" and ted_a.codici_soa == ["OG1"]
    assert db.scalar(select(Appalto).where(Appalto.cig == "X000000001")) is None

    scheda = client.get(f"/appalti/{a.id}").text
    assert "OG6 - Acquedotti" in scheda and "OS22 - Impianti di potabilizzazione" in scheda
    assert "Copia la scheda" in scheda and "Collega OpenAI" in scheda
    lista = client.get("/appalti?soa=OG6&regione=Abruzzo").text
    assert "Razionalizzazione del sistema depurativo" in lista
    assert "Razionalizzazione" not in client.get("/appalti?soa=OG1").text
    assert "Razionalizzazione" in client.get("/appalti?q=depurativo+SASI").text
    assert "Razionalizzazione" not in client.get("/appalti?q=depurativo+Milano").text


def test_opportunita_completo(client, crea_utente, accedi, db, monkeypatch):
    from app.jobs import attivita
    from app.models import BandoRequisiti, Opportunita

    utente = crea_utente()
    accedi(utente)
    azienda = _cliente(
        client,
        db,
        avvisi_attivi="1",
        regione="Abruzzo",
        dimensione_manuale="piccola",
        soa_testo="OG6 III\nOS22 II",
        cpv_interesse="45",
    )
    assert azienda.soa_possedute == [
        {"codice": "OG6", "classifica": "III"},
        {"codice": "OS22", "classifica": "II"},
    ]
    # codici CPV più precisi delle divisioni: salvati e mostrati di nuovo nel modulo
    precisa = _cliente(client, db, cpv_interesse="79", cpv_precisi="79710000 909")
    assert sorted(precisa.cpv_interesse) == ["79", "7971", "909"]
    assert 'value="7971 909"' in client.get(f"/clienti/{precisa.id}/modifica").text
    token = csrf(client, "/clienti/nuovo")
    r = client.post(
        "/clienti/nuovo",
        data={"csrf_token": token, "ragione_sociale": "Errata", "soa_testo": "OG99 II"},
    )
    assert r.status_code == 400 and "Categoria SOA sconosciuta" in r.text

    bando = _bando(db)
    db.add(
        BandoRequisiti(
            bando_id=bando.id,
            territorio="regionale",
            regioni=["Abruzzo"],
            dimensioni=["piccola"],
            provenienza={},
        )
    )
    appalto = Appalto(
        fonte="ted",
        id_esterno=f"prova-{uuid.uuid4().hex[:8]}",
        titolo="Depuratore Lanciano",
        regioni=["Abruzzo"],
        categorie_soa=[{"codice": "OG6", "tipo": "prevalente", "importo": "900000"}],
        codici_soa=["OG6"],
        natura=["lavori"],
        importo_stimato=Decimal("950000"),
        scadenza=date.today() + timedelta(days=20),
        raw={},
    )
    db.add(appalto)
    db.commit()

    r = client.post("/opportunita/aggiorna", data={"csrf_token": token})
    assert r.status_code == 303
    ops = list(db.scalars(select(Opportunita).where(Opportunita.azienda_id == azienda.id)))
    tipi = {("bando" if o.bando_id else "appalto"): o for o in ops}
    assert {"bando", "appalto"} <= set(tipi)
    assert any("classifica III sufficiente" in m for m in tipi["appalto"].motivi["soddisfatti"])

    pagina = client.get(f"/opportunita?cliente={azienda.id}").text
    assert "Depuratore Lanciano" in pagina and bando.titolo in pagina
    assert "Depuratore Lanciano" in client.get(f"/clienti/{azienda.id}").text

    # Email del mattino (posta simulata), una sola volta
    _configura_smtp(client)
    inviate = []
    monkeypatch.setattr(
        "app.comunicazioni.invia",
        lambda valori, a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)),
    )
    esito = attivita.opportunita()
    mie = [x for x in inviate if x[0] == utente.email]
    assert mie and "Depuratore Lanciano" in mie[0][2] and "Email inviate" in esito
    inviate.clear()
    attivita.opportunita()
    assert not [x for x in inviate if x[0] == utente.email]

    # Segnala al cliente, scarta, apri pratica
    o_app = tipi["appalto"]
    r = client.post(f"/opportunita/{o_app.id}/segnala", data={"csrf_token": token})
    assert "esito=ok" in r.headers["location"]
    a, oggetto, testo = inviate[-1]
    assert a == "maria@esempio.it" and "Depuratore Lanciano" in testo
    o_b = tipi["bando"]
    client.post(f"/opportunita/{o_b.id}/stato", data={"csrf_token": token, "stato": "scartata"})
    r = client.post(f"/opportunita/{o_app.id}/pratica", data={"csrf_token": token})
    assert r.headers["location"].startswith(f"/pratiche/nuova?azienda_id={azienda.id}&bando_testo=")
    db.expire_all()
    assert db.get(Opportunita, o_b.id).stato == "scartata"
    assert db.get(Opportunita, o_app.id).stato == "in_pratica"
    # un nuovo abbinamento non resuscita le opportunità già lavorate
    client.post("/opportunita/aggiorna", data={"csrf_token": token})
    db.expire_all()
    assert db.get(Opportunita, o_b.id).stato == "scartata"
