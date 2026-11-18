import io

import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from app.models import Allegato, AllegatoContenuto, Azienda
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


@pytest.fixture
def azienda(db):
    a = Azienda(ragione_sociale="Officine Prova S.r.l.", stato_cliente="attivo")
    db.add(a)
    db.commit()
    azienda_id = a.id
    yield a
    # Altri test contano tutte le aziende del database: non lasciarne in giro
    db.rollback()
    db.expunge_all()
    db.execute(delete(Azienda).where(Azienda.id == azienda_id))
    db.commit()


@pytest.fixture
def archivio_db(monkeypatch):
    monkeypatch.setattr(get_settings(), "archivio_allegati", "database")


def test_documento_dell_azienda_nel_database(client, crea_utente, accedi, db, azienda, archivio_db):
    accedi(crea_utente())
    token = csrf(client, f"/clienti/{azienda.id}")
    contenuto = b"%PDF-1.4 visura di prova"
    r = client.post(
        f"/clienti/{azienda.id}/documenti",
        data={"csrf_token": token},
        files={"file": ("Visura camerale è.pdf", io.BytesIO(contenuto), "application/pdf")},
    )
    assert r.status_code == 303 and r.headers["location"].endswith("#documenti")

    a = db.scalar(select(Allegato).where(Allegato.azienda_id == azienda.id))
    assert a is not None and a.pratica_id is None
    assert a.percorso.startswith("db/") and a.dimensione == len(contenuto)
    assert db.get(AllegatoContenuto, a.id).dati == contenuto

    pagina = client.get(f"/clienti/{azienda.id}").text
    assert "Visura camerale è.pdf" in pagina and "Dell'azienda" in pagina

    scaricato = client.get(f"/allegati/{a.id}")
    assert scaricato.status_code == 200 and scaricato.content == contenuto
    assert "attachment" in scaricato.headers["content-disposition"]
    assert "Visura%20camerale%20%C3%A8.pdf" in scaricato.headers["content-disposition"]

    r = client.post(f"/allegati/{a.id}/elimina", data={"csrf_token": token})
    assert r.status_code == 303 and r.headers["location"] == f"/clienti/{azienda.id}#documenti"
    aid = a.id
    db.expunge_all()
    assert db.scalar(select(Allegato).where(Allegato.id == aid)) is None
    assert db.scalar(select(AllegatoContenuto).where(AllegatoContenuto.allegato_id == aid)) is None


def test_documento_non_ammesso(client, crea_utente, accedi, db, azienda, archivio_db):
    accedi(crea_utente())
    token = csrf(client, f"/clienti/{azienda.id}")
    r = client.post(
        f"/clienti/{azienda.id}/documenti",
        data={"csrf_token": token},
        files={"file": ("programma.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
    )
    assert r.status_code == 303 and "msg_doc=" in r.headers["location"]
    assert "non ammesso" in client.get(r.headers["location"]).text
    assert db.scalar(select(Allegato).where(Allegato.azienda_id == azienda.id)) is None


def test_documento_dell_azienda_su_disco(
    client, crea_utente, accedi, db, azienda, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "cartella_allegati", str(tmp_path))
    accedi(crea_utente())
    token = csrf(client, f"/clienti/{azienda.id}")
    r = client.post(
        f"/clienti/{azienda.id}/documenti",
        data={"csrf_token": token},
        files={"file": ("bilancio.pdf", io.BytesIO(b"%PDF bilancio"), "application/pdf")},
    )
    assert r.status_code == 303
    a = db.scalar(select(Allegato).where(Allegato.azienda_id == azienda.id))
    assert not a.percorso.startswith("db/") and (tmp_path / a.percorso).is_file()
    assert db.get(AllegatoContenuto, a.id) is None
    assert client.get(f"/allegati/{a.id}").content == b"%PDF bilancio"
