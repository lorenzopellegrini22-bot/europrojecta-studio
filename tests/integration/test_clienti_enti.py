"""Un comune come cliente: modulo, scheda e opportunità (solo bandi per enti, niente gare)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

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

    # tipo non valido
    token = csrf(client, f"/clienti/{a.id}/modifica")
    dati = {"csrf_token": token, "ragione_sociale": nome, "tipo_soggetto": "x"}
    r = client.post(f"/clienti/{a.id}/modifica", data=dati)
    assert r.status_code == 400 and "Tipo di cliente non valido" in r.text
