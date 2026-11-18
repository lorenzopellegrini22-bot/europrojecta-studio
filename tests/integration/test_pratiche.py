"""Clienti, modelli di pratica, pratiche a fasi e scadenzario, su database reale."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    Azienda,
    Bando,
    Fonte,
    ModelloPratica,
    Pratica,
    PraticaEvento,
)
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _piva(db) -> str:
    """Una partita IVA valida e non ancora usata (i test condividono il database)."""
    from app.pratiche.servizio import partita_iva_valida

    usate = set(db.scalars(select(Azienda.partita_iva)))
    for _ in range(1000):
        base = f"{uuid.uuid4().int % 10**10:010d}"
        for c in range(10):
            if partita_iva_valida(base + str(c)) and base + str(c) not in usate:
                return base + str(c)
    raise AssertionError


def _modelli_esempio(client) -> None:
    token = csrf(client, "/modelli")
    assert client.post("/modelli/esempio", data={"csrf_token": token}).status_code == 303


def _cliente(client, db, **extra) -> Azienda:
    token = csrf(client, "/clienti/nuovo")
    nome = f"Prova {uuid.uuid4().hex[:6]} S.r.l."
    dati = {
        "csrf_token": token,
        "ragione_sociale": nome,
        "partita_iva": _piva(db),
        "referente_nome": "Maria",
        "referente_cognome": "Rossi",
        "referente_email": "maria@esempio.it",
        "regione": "Toscana",
        **extra,
    }
    r = client.post("/clienti/nuovo", data=dati)
    assert r.status_code == 303, r.text
    return db.scalar(select(Azienda).where(Azienda.ragione_sociale == nome))


def _bando(db) -> Bando:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_pratiche"))
    if fonte is None:
        fonte = Fonte(codice="prova_pratiche", nome="Prova", url="https://esempio.test")
        db.add(fonte)
        db.flush()
    b = Bando(
        fonte_id=fonte.id,
        id_esterno=uuid.uuid4().hex,
        titolo=f"Bando digitalizzazione {uuid.uuid4().hex[:4]}",
        stato="aperto",
        data_chiusura=date.today() + timedelta(days=20),
        raw={},
        hash_contenuto="x" * 64,
    )
    db.add(b)
    db.commit()
    return b


# --- Clienti -----------------------------------------------------------------------------


def test_cliente_validazioni_e_referente(client, crea_utente, accedi, db):
    accedi(crea_utente("collaboratore"))
    token = csrf(client, "/clienti/nuovo")
    r = client.post(
        "/clienti/nuovo",
        data={"csrf_token": token, "ragione_sociale": "", "partita_iva": "12345678901"},
    )
    assert r.status_code == 400
    assert "ragione sociale è obbligatoria" in r.text
    assert "Partita IVA non valida" in r.text

    a = _cliente(client, db)
    assert a.sede_legale.regione == "Toscana"
    assert a.referente_principale.nome_completo == "Maria Rossi"

    # stessa partita IVA: rifiutata
    r = client.post(
        "/clienti/nuovo",
        data={"csrf_token": token, "ragione_sociale": "Doppione", "partita_iva": a.partita_iva},
    )
    assert r.status_code == 400 and "già usata" in r.text

    r = client.get(f"/clienti/{a.id}")
    assert r.status_code == 200 and a.ragione_sociale in r.text
    assert client.get("/clienti?q=Prova").status_code == 200

    # il collaboratore non può eliminare clienti
    r = client.post(f"/clienti/{a.id}/elimina", data={"csrf_token": token})
    assert r.status_code == 403


# --- Modelli -----------------------------------------------------------------------------


def test_modelli_di_esempio_e_modifica(client, crea_utente, accedi, db):
    accedi(crea_utente())
    _modelli_esempio(client)
    _modelli_esempio(client)  # la seconda volta non crea doppioni
    modelli = list(db.scalars(select(ModelloPratica)))
    nomi = [m.nome for m in modelli]
    assert len(nomi) == len(set(nomi)) >= 3
    completo = next(m for m in modelli if m.nome.startswith("Contributo a fondo perduto"))
    assert len(completo.fasi) == 9

    token = csrf(client, f"/modelli/{completo.id}")
    r = client.post(f"/modelli/{completo.id}/duplica", data={"csrf_token": token})
    assert r.status_code == 303
    copia_id = int(r.headers["location"].rsplit("/", 1)[1])
    copia = db.get(ModelloPratica, copia_id)
    db.refresh(copia)
    assert len(copia.fasi) == 9

    # aggiunta di un'operazione "documenti", spostamento ed eliminazione di una fase
    fase = copia.fasi[0]
    r = client.post(
        f"/modelli/{copia.id}/fasi/{fase.id}/operazioni",
        data={
            "csrf_token": token,
            "titolo": "DURC",
            "tipo": "documenti",
            "documenti": "DURC regolare\n\n  Certificato antimafia  ",
            "giorni_previsti": "4",
        },
    )
    assert r.status_code == 303
    db.expire_all()
    op = db.get(ModelloPratica, copia.id).fasi[0].operazioni[-1]
    assert op.documenti == ["DURC regolare", "Certificato antimafia"]
    assert op.giorni_previsti == 4 and op.campi is None

    ultima = db.get(ModelloPratica, copia.id).fasi[-1]
    client.post(f"/modelli/{copia.id}/fasi/{ultima.id}", data={"csrf_token": token, "azione": "su"})
    client.post(
        f"/modelli/{copia.id}/fasi/{ultima.id}", data={"csrf_token": token, "azione": "elimina"}
    )
    db.expire_all()
    fasi = db.get(ModelloPratica, copia.id).fasi
    assert [f.numero for f in fasi] == list(range(1, 9))


def test_collaboratore_non_modifica_i_modelli(client, crea_utente, accedi):
    accedi(crea_utente("collaboratore"))
    token = csrf(client, "/modelli")
    assert client.get("/modelli").status_code == 200
    r = client.post("/modelli/nuovo", data={"csrf_token": token, "nome": "Mio"})
    assert r.status_code == 403


# --- Pratiche ----------------------------------------------------------------------------


def test_pratica_completa(client, crea_utente, accedi, db):
    titolare = crea_utente()
    accedi(titolare)
    _modelli_esempio(client)
    azienda = _cliente(client, db)
    bando = _bando(db)
    modello = db.scalar(
        select(ModelloPratica).where(ModelloPratica.nome.like("Contributo a fondo perduto%"))
    )

    # Nuova pratica dalla scheda del bando
    assert f"/pratiche/nuova?bando_id={bando.id}" in client.get(f"/bandi/{bando.id}").text
    r = client.get(f"/pratiche/nuova?bando_id={bando.id}&azienda_id={azienda.id}")
    assert r.status_code == 200 and bando.titolo in r.text
    token = csrf(client, "/pratiche/nuova")
    r = client.post(
        "/pratiche/nuova",
        data={
            "csrf_token": token,
            "azienda_id": str(azienda.id),
            "bando_id": str(bando.id),
            "modello_id": str(modello.id),
            "responsabile_id": str(titolare.id),
        },
    )
    assert r.status_code == 303, r.text
    pratica = db.get(Pratica, int(r.headers["location"].rsplit("/", 1)[1]))
    oggi = date.today()
    assert pratica.codice == f"{oggi.year}-{pratica.codice[-4:]}"
    assert pratica.scadenza_presentazione == bando.data_chiusura  # dalla chiusura del bando
    assert len(pratica.fasi) == 9
    assert pratica.fase_attiva.numero == 1
    # scadenze delle operazioni calcolate all'avvio della fase
    assert pratica.fasi[0].operazioni[0].scadenza == oggi + timedelta(days=3)
    assert pratica.fasi[1].operazioni[0].scadenza is None

    pagina = client.get(f"/pratiche/{pratica.id}").text
    assert "Verifica requisiti e prefattibilità" in pagina

    # Checklist e comunicazione nella fase 4
    fase4 = pratica.fasi[3]
    r = client.post(
        f"/pratiche/{pratica.id}/fasi/{fase4.id}/azione/attiva", data={"csrf_token": token}
    )
    assert r.status_code == 303
    db.expire_all()
    pratica = db.get(Pratica, pratica.id)
    fase4 = pratica.fasi[3]
    assert pratica.fase_attiva.id == fase4.id
    assert pratica.fasi[0].stato == "da_iniziare"
    documenti = fase4.operazioni[0]
    visura = documenti.documenti[0]
    client.post(
        f"/pratiche/{pratica.id}/operazioni/{documenti.id}",
        data={"csrf_token": token, f"documento_{visura.id}": "ricevuto", "nuovo_documento": "DURC"},
    )
    pagina = client.get(f"/pratiche/{pratica.id}?fase=4").text
    # email con i segnaposto sostituiti: il documento ricevuto non è più tra i mancanti
    assert "Gentile Maria Rossi" in pagina
    assert bando.titolo in pagina
    assert "- Preventivi di spesa" in pagina and "- DURC" in pagina
    assert "- Visura camerale aggiornata" not in pagina
    assert "mailto:maria@esempio.it" in pagina

    # Operazione fatta, fase completata: parte la successiva
    client.post(
        f"/pratiche/{pratica.id}/operazioni/{documenti.id}/stato",
        data={"csrf_token": token, "stato": "fatta"},
    )
    client.post(
        f"/pratiche/{pratica.id}/fasi/{fase4.id}/azione/completa", data={"csrf_token": token}
    )
    db.expire_all()
    pratica = db.get(Pratica, pratica.id)
    assert pratica.fasi[3].stato == "completata"
    assert pratica.fase_attiva.numero == 5
    assert pratica.fasi[3].operazioni[0].completata_da == titolare.id

    # Dati della pratica e cambio di stato con diario
    r = client.post(
        f"/pratiche/{pratica.id}/dati",
        data={
            "csrf_token": token,
            "stato": "presentata",
            "responsabile_id": str(titolare.id),
            "importo_richiesto": "45.000,50",
            "protocollo": "PROT-123",
            "nota_stato": "Inviata via PEC",
        },
    )
    assert r.status_code == 303
    db.expire_all()
    pratica = db.get(Pratica, pratica.id)
    assert pratica.stato == "presentata"
    assert str(pratica.importo_richiesto) == "45000.50"
    note = [
        e.nota
        for e in db.scalars(select(PraticaEvento).where(PraticaEvento.pratica_id == pratica.id))
    ]
    assert any("Domanda presentata" in n and "Inviata via PEC" in n for n in note)
    assert any("Fase 4" in n and "completata" in n for n in note)

    # Riepilogo, elenco ed export CSV
    assert "PROT-123" in client.get(f"/pratiche/{pratica.id}/riepilogo").text
    assert pratica.codice in client.get(f"/pratiche?q={azienda.ragione_sociale}").text
    csv = client.get(f"/pratiche/export.csv?q={pratica.codice}")
    assert csv.status_code == 200
    assert csv.text.startswith("﻿Codice;Cliente")
    assert f"{pratica.codice};{azienda.ragione_sociale}" in csv.text
    assert "45000,50" in csv.text
    assert db.scalar(
        select(AuditLog).where(AuditLog.entita == "pratica", AuditLog.azione == "esporta")
    )

    # Il cliente con pratiche non si può eliminare
    r = client.post(f"/clienti/{azienda.id}/elimina", data={"csrf_token": token})
    assert r.status_code == 400


def test_pratica_senza_bando_nel_database_e_senza_modello(client, crea_utente, accedi, db):
    accedi(crea_utente("collaboratore"))
    azienda = _cliente(client, db)
    token = csrf(client, "/pratiche/nuova")
    r = client.post("/pratiche/nuova", data={"csrf_token": token, "azienda_id": str(azienda.id)})
    assert r.status_code == 400 and "Scegli un bando" in r.text
    r = client.post(
        "/pratiche/nuova",
        data={
            "csrf_token": token,
            "azienda_id": str(azienda.id),
            "bando_testo": "Bando camerale 2026",
        },
    )
    assert r.status_code == 303
    pratica = db.get(Pratica, int(r.headers["location"].rsplit("/", 1)[1]))
    assert pratica.nome_bando == "Bando camerale 2026"
    assert [f.nome for f in pratica.fasi] == ["Lavorazione"]
    # il collaboratore non elimina pratiche
    assert (
        client.post(f"/pratiche/{pratica.id}/elimina", data={"csrf_token": token}).status_code
        == 403
    )


def test_ricerca_bandi_per_nuova_pratica(client, crea_utente, accedi, db):
    accedi(crea_utente())
    bando = _bando(db)
    r = client.get("/pratiche/cerca-bandi?q=digitalizzazione")
    assert r.status_code == 200 and bando.titolo in r.text
    assert client.get("/pratiche/cerca-bandi?q=di").text.strip() == ""


# --- Scadenzario -------------------------------------------------------------------------


def test_scadenzario(client, crea_utente, accedi, db):
    utente = crea_utente()
    accedi(utente)
    _modelli_esempio(client)
    azienda = _cliente(client, db)
    modello = db.scalar(
        select(ModelloPratica).where(ModelloPratica.nome.like("Voucher a sportello%"))
    )
    token = csrf(client, "/pratiche/nuova")
    r = client.post(
        "/pratiche/nuova",
        data={
            "csrf_token": token,
            "azienda_id": str(azienda.id),
            "bando_testo": "Voucher di prova",
            "modello_id": str(modello.id),
            "responsabile_id": str(utente.id),
            "scadenza_presentazione": (date.today() + timedelta(days=5)).isoformat(),
        },
    )
    pratica = db.get(Pratica, int(r.headers["location"].rsplit("/", 1)[1]))
    r = client.get("/scadenzario?mie=1")
    assert r.status_code == 200
    assert "Verifica dei requisiti e della dotazione residua" in r.text  # entro 2 giorni
    assert "Presentazione della domanda" in r.text
    assert pratica.codice in r.text
    assert "mancano 2 giorni" in r.text
    home = client.get("/").text
    assert "Le mie scadenze" in home and pratica.codice in home
