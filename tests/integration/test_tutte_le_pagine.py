# ruff: noqa: E501
"""Giro di controllo: ogni pagina dell'app (GET) si apre senza errori per ogni tipo di utente.
Gli indirizzi con un numero usano oggetti veri creati qui, oppure un numero inesistente
(deve dare 404, mai un errore del server)."""

import random
import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.main import app
from app.models import Appalto, Azienda, Bando, Fonte, Opportunita
from app.pratiche.servizio import crea_pratica

pytestmark = pytest.mark.integration

INESISTENTE = "999999999"
# Pagine pesanti o che fanno chiamate esterne: provate altrove
ESCLUSE = {"/export/completo.zip", "/cron/{nome}"}


def _pagine() -> list[str]:
    return sorted(
        p for p, info in app.openapi()["paths"].items() if "get" in info and p not in ESCLUSE
    )


def _oggetti(db, utente) -> dict[str, str]:
    a = Azienda(
        ragione_sociale=f"Giro Pagine {uuid.uuid4().hex[:6]} S.r.l.",
        partita_iva="".join(random.choices("0123456789", k=11)),
        stato_cliente="attivo",
    )
    db.add(a)
    fonte = Fonte(codice=f"giro_{uuid.uuid4().hex[:6]}", nome="Giro", url="https://esempio.test")
    db.add(fonte)
    db.flush()
    b = Bando(fonte_id=fonte.id, id_esterno=uuid.uuid4().hex, titolo="Bando del giro", raw={},
              stato="aperto", hash_contenuto="b" * 64, data_chiusura=date.today() + timedelta(days=9))  # fmt: skip
    g = Appalto(fonte="ted", id_esterno=f"{random.randint(100000, 999999)}-2026", titolo="Gara del giro",
                raw={}, scadenza=date.today() + timedelta(days=9), documenti=[])  # fmt: skip
    db.add_all([b, g])
    db.flush()
    o = Opportunita(azienda_id=a.id, bando_id=b.id, punteggio=70, motivi={}, stato="nuova",
                    trovata_il=datetime.now(UTC))  # fmt: skip
    db.add(o)
    p = crea_pratica(db, utente, a, date.today(), bando_testo="Bando del giro", responsabile=utente)
    db.commit()
    return {
        "azienda_id": str(a.id),
        "pratica_id": str(p.id),
        "bando_id": str(b.id),
        "appalto_id": str(g.id),
        "oid": str(o.id),
        "nome": "inventato.ics",
        "token": "inventato",
    }


def _indirizzo(percorso: str, valori: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: valori.get(m.group(1), INESISTENTE), percorso)


@pytest.mark.parametrize(
    ("ruolo", "sezioni"),
    [("titolare", None), ("collaboratore", ["bandi"]), ("lettura", ["pratiche", "opportunita"])],
)
def test_ogni_pagina_si_apre(client, crea_utente, accedi, db, ruolo, sezioni):
    titolare = crea_utente("titolare")
    valori = _oggetti(db, titolare)
    u = crea_utente(ruolo)
    if sezioni is not None:
        u.sezioni = sezioni
        db.commit()
    accedi(u)
    errori = []
    for percorso in _pagine():
        url = _indirizzo(percorso, valori)
        r = client.get(url, follow_redirects=False)
        if r.status_code >= 500:
            errori.append(f"{url}: {r.status_code}")
    assert not errori, "Pagine con errore: " + ", ".join(errori)
    assert client.get("/").status_code == 200
