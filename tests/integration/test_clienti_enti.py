"""Un comune come cliente: modulo, scheda e opportunità (solo bandi per enti, niente gare)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import Azienda, Bando, BandoRequisiti, Fonte
from app.models.appalti import Appalto
from app.models.opportunita import Opportunita
from app.opportunita.abbinamento import abbina
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


def _bando(db, titolo: str, tipologie: list[str]) -> Bando:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_enti"))
    if fonte is None:
        fonte = Fonte(codice="prova_enti", nome="Prova enti", url="https://esempio.test")
        db.add(fonte)
        db.flush()
    b = Bando(
        fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo=titolo, stato="aperto",
        data_chiusura=date.today() + timedelta(days=30), raw={}, hash_contenuto="x" * 64,
    )  # fmt: skip
    db.add(b)
    db.flush()
    db.add(BandoRequisiti(bando_id=b.id, provenienza={}, territorio="regionale",
                          regioni=["Lazio"], tipologie_soggetto=tipologie))  # fmt: skip
    return b


def test_comune_come_cliente(client, crea_utente, accedi, db):
    accedi(crea_utente())
    token = csrf(client, "/clienti/nuovo")
    modulo = client.get("/clienti/nuovo").text
    assert 'name="tipo_soggetto"' in modulo and 'name="abitanti"' in modulo
    nome = f"Comune di Bassano in Teverina {uuid.uuid4().hex[:4]}"
    r = client.post(
        "/clienti/nuovo",
        data={
            "csrf_token": token, "ragione_sociale": nome, "tipo_soggetto": "ente_pubblico",
            "abitanti": "1.300", "forma_giuridica": "Comune", "codice_fiscale": "80008590566",
            "comune_testo": "Bassano in Teverina", "provincia": "VT", "regione": "Lazio",
            "avvisi_attivi": "1",
        },
    )  # fmt: skip
    assert r.status_code == 303, r.text
    a = db.scalar(select(Azienda).where(Azienda.ragione_sociale == nome))
    assert a.tipo_soggetto == "ente_pubblico" and a.abitanti == 1300
    scheda = client.get(f"/clienti/{a.id}").text
    assert "Ente pubblico" in scheda and "1.300" in scheda and "<dt>ATECO</dt>" not in scheda

    per_enti = _bando(db, f"Parcheggi nei Comuni del Lazio {uuid.uuid4().hex[:4]}",
                      ["Ente Pubblico"])  # fmt: skip
    per_imprese = _bando(db, f"Voucher per le imprese {uuid.uuid4().hex[:4]}", ["Impresa"])
    gara = Appalto(fonte="ted", id_esterno=f"enti-{uuid.uuid4().hex[:8]}", titolo="Gara",
                   scadenza=date.today() + timedelta(days=30), raw={})  # fmt: skip
    db.add(gara)
    db.commit()
    abbina(db)
    trovate = list(db.scalars(select(Opportunita).where(Opportunita.azienda_id == a.id)))
    bandi = {o.bando_id for o in trovate}
    assert per_enti.id in bandi and per_imprese.id not in bandi
    assert all(o.appalto_id is None for o in trovate)  # un comune le gare non le fa

    # senza comune nel modulo la sede si ricava dal nome
    token = csrf(client, "/clienti/nuovo")
    nome2 = f"Comune di Celleno {uuid.uuid4().hex[:4]}"
    dati = {"csrf_token": token, "ragione_sociale": nome2, "tipo_soggetto": "ente_pubblico"}
    assert client.post("/clienti/nuovo", data=dati).status_code == 303
    a2 = db.scalar(select(Azienda).where(Azienda.ragione_sociale == nome2))
    assert a2.sede_legale is not None and a2.sede_legale.comune_testo.startswith("Celleno")

    # ricerca e filtro per tipo nell'elenco clienti
    assert nome in client.get("/clienti?q=Bassano in Teverina").text
    assert nome in client.get("/clienti?tipo=ente_pubblico").text
    assert nome not in client.get("/clienti?tipo=impresa").text

    # tipo non valido
    token = csrf(client, f"/clienti/{a.id}/modifica")
    dati = {"csrf_token": token, "ragione_sociale": nome, "tipo_soggetto": "x"}
    r = client.post(f"/clienti/{a.id}/modifica", data=dati)
    assert r.status_code == 400 and "Tipo di cliente non valido" in r.text


def test_eliminazione_multipla_definitiva(client, crea_utente, accedi, db):
    from app.models import ElementoCestino
    from tests.integration.test_pratiche import _cliente

    accedi(crea_utente())
    ida, idb, idc = (_cliente(client, db).id for _ in range(3))
    lista = client.get("/clienti").text
    assert 'name="ids"' in lista and "L'azione è definitiva" in lista
    token = csrf(client, "/clienti")
    # senza la parola di conferma non si elimina niente
    r = client.post("/clienti/elimina-selezionati",
                    data={"csrf_token": token, "ids": [ida, idb], "conferma": "si"})  # fmt: skip
    assert r.status_code == 303 and "annullata" in r.headers["location"]
    assert db.get(Azienda, ida) is not None
    # un cliente con pratica si salta
    from tests.integration.test_pratiche import _bando

    tok = csrf(client, f"/pratiche/nuova?azienda_id={idc}")
    bando = _bando(db)
    r = client.post("/pratiche/nuova",
                    data={"csrf_token": tok, "azienda_id": idc, "bando_id": bando.id})  # fmt: skip
    assert r.status_code == 303, r.text
    nel_cestino = db.scalar(select(func.count()).select_from(ElementoCestino))
    r = client.post("/clienti/elimina-selezionati", data={
        "csrf_token": token, "ids": [ida, idb, idc], "conferma": "elimina"})  # fmt: skip
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Azienda, ida) is None and db.get(Azienda, idb) is None
    assert db.get(Azienda, idc) is not None and "pratiche" in r.headers["location"]
    # definitiva: non resta nel cestino
    assert db.scalar(select(func.count()).select_from(ElementoCestino)) == nel_cestino


def test_eliminazione_multipla_solo_titolare(client, crea_utente, accedi, db):
    accedi(crea_utente("collaboratore"))
    assert 'name="ids"' not in client.get("/clienti").text
    token = csrf(client, "/clienti/nuovo")
    r = client.post("/clienti/elimina-selezionati",
                    data={"csrf_token": token, "ids": ["1"], "conferma": "ELIMINA"})  # fmt: skip
    assert r.status_code == 403
