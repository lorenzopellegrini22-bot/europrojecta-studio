"""Cestino: quello che si elimina si ripristina per 30 giorni, con tutto ciò che conteneva."""

import io
import random
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.cestino import servizio as cestino
from app.models import (
    Allegato,
    Azienda,
    Commento,
    ElementoCestino,
    Menzione,
    Pratica,
    PraticaFase,
    Progetto,
    Referente,
    Scadenza,
)
from app.pratiche import allegati as archivio
from app.pratiche.servizio import crea_pratica
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def allegati_temporanei(tmp_path, monkeypatch):
    monkeypatch.setattr(archivio, "cartella", lambda: tmp_path / "allegati")


@pytest.fixture(autouse=True)
def pulizia(db):
    """Altri test valutano tutte le aziende del database: non lasciarne in giro."""
    yield
    db.rollback()
    db.expunge_all()
    prova = (Azienda.ragione_sociale.like("Cestino Prova %")) | (
        Azienda.ragione_sociale == "Omonimo S.r.l."
    )
    ids = list(db.scalars(select(Azienda.id).where(prova)))
    db.execute(delete(Pratica).where(Pratica.azienda_id.in_(ids)))
    db.execute(delete(Azienda).where(Azienda.id.in_(ids)))
    db.execute(delete(ElementoCestino))
    db.commit()


def _piva() -> str:
    return "".join(random.choices("0123456789", k=11))


def _cliente(db, piva: str | None = None) -> Azienda:
    a = Azienda(
        ragione_sociale=f"Cestino Prova {uuid.uuid4().hex[:6]} S.r.l.",
        partita_iva=piva or _piva(),
        stato_cliente="attivo",
    )
    a.referenti.append(Referente(nome="Lucia", cognome="Verdi", principale=True))
    db.add(a)
    db.flush()
    db.add(Progetto(azienda_id=a.id, titolo="Nuovo capannone"))
    db.add(Scadenza(azienda_id=a.id, titolo="Rinnovo DURC", data=date.today()))
    db.commit()
    return a


def _carica_documento(client, token, azienda_id: int) -> Allegato:
    r = client.post(
        f"/clienti/{azienda_id}/documenti",
        data={"csrf_token": token},
        files={"file": ("visura.pdf", io.BytesIO(b"%PDF-1.4 visura"), "application/pdf")},
    )
    assert r.status_code == 303
    return r


def test_cliente_eliminato_e_ripristinato_con_tutto(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(db)
    aid, nome = a.id, a.ragione_sociale
    token = csrf(client, f"/clienti/{aid}")
    _carica_documento(client, token, aid)
    allegato = db.scalar(select(Allegato).where(Allegato.azienda_id == aid))
    alid = allegato.id
    file = archivio.percorso_assoluto(allegato.percorso)
    assert file.is_file()

    r = client.post(f"/clienti/{aid}/elimina", data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/clienti?cestino=")
    db.expire_all()
    assert db.get(Azienda, aid) is None
    assert file.is_file()  # il file resta finché l'elemento è nel cestino

    # avviso con «Annulla» nella pagina di ritorno e voce nel cestino
    pagina = client.get(r.headers["location"]).text
    assert "spostato nel cestino" in pagina and "Annulla" in pagina
    assert nome in client.get("/cestino").text

    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "azienda",
                                                ElementoCestino.oggetto_id == aid))  # fmt: skip
    eid = e.id
    r = client.post(f"/cestino/{e.id}/ripristina", data={"csrf_token": token})
    assert r.status_code == 303 and r.headers["location"].startswith(
        f"/clienti/{aid}?ripristinato="
    )
    db.expire_all()
    a = db.get(Azienda, aid)
    assert a is not None and a.ragione_sociale == nome
    assert [x.nome for x in a.referenti] == ["Lucia"]
    assert db.scalar(select(Progetto).where(Progetto.azienda_id == aid)).titolo == "Nuovo capannone"
    assert db.scalar(select(Scadenza).where(Scadenza.azienda_id == aid)).titolo == "Rinnovo DURC"
    assert client.get(f"/allegati/{alid}").content == b"%PDF-1.4 visura"
    assert "ripristinato dal cestino" in client.get(r.headers["location"]).text
    assert db.scalar(select(ElementoCestino.id).where(ElementoCestino.id == eid)) is None


def test_pratica_con_note_e_promemoria_collegato(client, crea_utente, accedi, db):
    u = crea_utente()
    accedi(u)
    a = _cliente(db)
    p = crea_pratica(db, u, a, date.today(), bando_testo="Bando digitale", responsabile=u)
    db.flush()
    nota = Commento(oggetto="pratica", oggetto_id=p.id, utente_id=u.id, testo="Chiamare @mario")
    nota.menzioni.append(Menzione(utente_id=u.id))
    db.add(nota)
    promemoria = db.scalar(select(Scadenza).where(Scadenza.azienda_id == a.id))
    promemoria.pratica_id = p.id
    db.commit()
    pid, fasi, nid, sid = p.id, len(p.fasi), nota.id, promemoria.id
    token = csrf(client, f"/pratiche/{pid}")

    r = client.post(f"/pratiche/{pid}/elimina", data={"csrf_token": token})
    assert r.headers["location"].startswith("/pratiche?cestino=")
    db.expire_all()
    assert db.get(Pratica, pid) is None and db.get(Commento, nid) is None
    assert db.get(Scadenza, sid).pratica_id is None  # l'eliminazione scollega il promemoria

    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "pratica",
                                                ElementoCestino.oggetto_id == pid))  # fmt: skip
    client.post(f"/cestino/{e.id}/ripristina", data={"csrf_token": token})
    db.expire_all()
    p = db.get(Pratica, pid)
    assert p is not None and len(p.fasi) == fasi
    assert db.scalar(select(PraticaFase).where(PraticaFase.pratica_id == pid)) is not None
    assert db.get(Commento, nid).testo == "Chiamare @mario"
    assert len(db.get(Commento, nid).menzioni) == 1
    assert db.get(Scadenza, sid).pratica_id == pid  # collegamento ricreato


def test_ripristino_impossibile_spiega_perche(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(db)
    aid, piva = a.id, a.partita_iva
    rif = a.referenti[0].id
    token = csrf(client, f"/clienti/{aid}")
    # referente eliminato, poi il cliente: il referente da solo non ha più dove tornare
    client.post(f"/clienti/{aid}/referenti/{rif}/elimina", data={"csrf_token": token})
    client.post(f"/clienti/{aid}/elimina", data={"csrf_token": token})
    db.expire_all()
    e_ref = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "referente",
                                                    ElementoCestino.oggetto_id == rif))  # fmt: skip
    r = client.post(f"/cestino/{e_ref.id}/ripristina", data={"csrf_token": token})
    assert "esito=errore" in r.headers["location"]
    assert "ripristina prima" in client.get(r.headers["location"]).text
    assert db.get(ElementoCestino, e_ref.id) is not None  # resta nel cestino

    # un nuovo cliente con la stessa partita IVA blocca il ripristino del vecchio
    nuovo = Azienda(ragione_sociale="Omonimo S.r.l.", partita_iva=piva, stato_cliente="attivo")
    db.add(nuovo)
    db.commit()
    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "azienda",
                                                ElementoCestino.oggetto_id == aid))  # fmt: skip
    r = client.post(f"/cestino/{e.id}/ripristina", data={"csrf_token": token})
    assert "esito=errore" in r.headers["location"]
    assert "partita IVA" in client.get(r.headers["location"]).text
    db.expire_all()
    assert db.get(Azienda, aid) is None


def test_svuotamento_dopo_30_giorni_toglie_anche_i_file(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(db)
    token = csrf(client, f"/clienti/{a.id}")
    _carica_documento(client, token, a.id)
    allegato = db.scalar(select(Allegato).where(Allegato.azienda_id == a.id))
    alid = allegato.id
    file = archivio.percorso_assoluto(allegato.percorso)
    client.post(f"/allegati/{alid}/elimina", data={"csrf_token": token})
    db.expire_all()
    e = db.scalar(
        select(ElementoCestino).where(
            ElementoCestino.tabella == "allegato", ElementoCestino.oggetto_id == alid
        )
    )
    eid = e.id
    assert file.is_file()
    # fra 29 giorni resta, fra 31 sparisce
    assert cestino.svuota_scaduti(db, datetime.now(UTC) + timedelta(days=29)) == 0
    assert cestino.svuota_scaduti(db, datetime.now(UTC) + timedelta(days=31)) >= 1
    db.commit()
    assert (
        db.scalar(select(ElementoCestino.id).where(ElementoCestino.id == eid)) is None
        and not file.exists()
    )


def test_elimina_per_sempre_e_svuota(client, crea_utente, accedi, db):
    accedi(crea_utente())
    a = _cliente(db)
    token = csrf(client, f"/clienti/{a.id}")
    client.post(f"/clienti/{a.id}/elimina", data={"csrf_token": token})
    e = db.scalar(select(ElementoCestino).where(ElementoCestino.oggetto_id == a.id,
                                                ElementoCestino.tabella == "azienda"))  # fmt: skip
    eid = e.id
    r = client.post(f"/cestino/{e.id}/elimina", data={"csrf_token": token})
    assert "eliminato per sempre" in client.get(r.headers["location"]).text
    db.expire_all()
    assert db.scalar(select(ElementoCestino.id).where(ElementoCestino.id == eid)) is None
    r = client.post("/cestino/svuota", data={"csrf_token": token})
    assert "Cestino svuotato" in client.get(r.headers["location"]).text
    assert db.scalar(select(ElementoCestino.id).limit(1)) is None


def test_cestino_solo_per_il_titolare(client, crea_utente, accedi, db):
    collaboratore = crea_utente("collaboratore")
    accedi(collaboratore)
    assert client.get("/cestino").status_code == 403
    assert 'href="/cestino"' not in client.get("/oggi").text
    a = _cliente(db)
    token = csrf(client, f"/clienti/{a.id}")
    r = client.post(f"/clienti/{a.id}/referenti/{a.referenti[0].id}/elimina",
                    data={"csrf_token": token})  # fmt: skip
    pagina = client.get(r.headers["location"]).text
    assert "spostato nel cestino" in pagina and "Il titolare può recuperarlo" in pagina
    assert "Annulla" not in pagina
    e = db.scalar(select(ElementoCestino).where(ElementoCestino.tabella == "referente")
                  .order_by(ElementoCestino.id.desc()))  # fmt: skip
    assert client.post(f"/cestino/{e.id}/ripristina", data={"csrf_token": token}).status_code == 403
