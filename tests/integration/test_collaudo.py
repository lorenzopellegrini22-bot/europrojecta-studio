"""Comando di collaudo: valutazione delle coppie etichettate e report."""

import uuid

import pytest

from app import collaudo
from app.models import BandoRequisiti, Fonte

pytestmark = pytest.mark.integration


def test_collaudo_report(db):
    from app.models import Bando

    fonte = db.query(Fonte).filter(Fonte.codice == "prova_collaudo").one_or_none()
    if fonte is None:
        fonte = Fonte(codice="prova_collaudo", nome="Prova", url="https://esempio.test")
        db.add(fonte)
        db.flush()

    def bando(titolo, **req):
        b = Bando(
            fonte_id=fonte.id,
            id_esterno=uuid.uuid4().hex,
            titolo=titolo,
            stato="aperto",
            raw={},
            hash_contenuto="x" * 64,
        )
        db.add(b)
        db.flush()
        db.add(BandoRequisiti(bando_id=b.id, provenienza={}, **req))
        return b

    lazio = bando(
        "Bando Lazio PMI", territorio="regionale", regioni=["Lazio"], dimensioni=["piccola"]
    )
    marche = bando("Bando Marche", territorio="regionale", regioni=["Marche"])
    db.commit()
    a01 = collaudo.AZIENDE_PROVA[0]  # Lazio, piccola
    dati = {
        "aziende": [a01],
        "bandi": [
            {"fonte": "prova_collaudo", "id_esterno": lazio.id_esterno},
            {"fonte": "prova_collaudo", "id_esterno": marche.id_esterno},
        ],
        "etichette": [
            {
                "azienda": "A01",
                "bando": f"prova_collaudo:{lazio.id_esterno}",
                "atteso": "compatibile",
            },
            {
                "azienda": "A01",
                "bando": f"prova_collaudo:{marche.id_esterno}",
                "atteso": "non_compatibile",
            },
        ],
    }
    r = collaudo.esegui(db, dati)
    assert r.precisione == 1.0 and not r.esclusioni_sbagliate and r.superato
    testo = collaudo.report(r)
    assert "SUPERATO" in testo and "Bando per altre regioni" in testo

    # Se il consulente dice che il bando Marche era compatibile, è un'esclusione sbagliata
    dati["etichette"][1]["atteso"] = "compatibile"
    r = collaudo.esegui(db, dati)
    assert len(r.esclusioni_sbagliate) == 1 and not r.superato
    assert "Esclusioni sbagliate" in collaudo.report(r)
