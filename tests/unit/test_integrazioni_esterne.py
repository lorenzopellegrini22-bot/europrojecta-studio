"""Registro Imprese (Openapi), TED, posta, allegati e prefattibilità: senza rete e senza DB."""

import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.appalti import ted
from app.integrazioni import posta
from app.integrazioni import registro_imprese as ri
from app.integrazioni.registro import INTEGRAZIONI
from app.pratiche import allegati
from app.pratiche.prefattibilita import esito_suggerito, voci_automatiche

FIXTURES = Path(__file__).parents[1] / "fixtures"

# Esempio della specifica ufficiale Openapi (GET /IT-advanced, risposta 200)
ESEMPIO_ADVANCED = {
    "data": [
        {
            "taxCode": "12485671007",
            "companyName": "OPENAPI S.P.A.",
            "vatCode": "12485671007",
            "address": {
                "registeredOffice": {
                    "streetName": "VIALE F TOMMASO MARINETTI 221",
                    "town": "ROMA",
                    "province": "RM",
                    "zipCode": "00143",
                    "region": {"code": "12", "description": "LAZIO"},
                }
            },
            "activityStatus": "ATTIVA",
            "atecoClassification": {
                "ateco": {"code": "621", "description": "Programmazione informatica"},
                "ateco2007": {"code": "6201", "description": "Produzione di software"},
            },
            "detailedLegalForm": {"code": "SP", "description": "SOCIETA' PER AZIONI"},
            "pec": "openapi@legalmail.it",
            "sdiCode": "USAL8PV",
            "balanceSheets": {"last": {"year": 2023, "turnover": 1500000, "employees": 42}},
        }
    ],
    "success": True,
    "message": "",
    "error": None,
}
VALORI = {"token": "tok", "ambiente": "sandbox", "livello": "advanced"}


def _client(stato: int, corpo: dict | None = None, registro: list | None = None) -> httpx.Client:
    def gestore(req: httpx.Request) -> httpx.Response:
        if registro is not None:
            registro.append(req)
        return httpx.Response(stato, json=corpo or {})

    return httpx.Client(transport=httpx.MockTransport(gestore))


# --- Registro Imprese --------------------------------------------------------------------


def test_registro_imprese_interpreta_esempio_ufficiale():
    richieste: list[httpx.Request] = []
    d = ri.cerca(VALORI, "IT 12485671007", _client(200, ESEMPIO_ADVANCED, richieste))
    r = richieste[0]
    assert str(r.url) == "https://test.company.openapi.com/IT-advanced/12485671007"
    assert r.headers["authorization"] == "Bearer tok"
    assert d.ragione_sociale == "OPENAPI S.P.A."
    assert d.partita_iva == "12485671007"
    assert d.comune_testo == "Roma" and d.provincia == "RM" and d.cap == "00143"
    assert d.regione == "Lazio"
    assert d.pec == "openapi@legalmail.it" and d.codice_sdi == "USAL8PV"
    assert d.ateco_codice == "62.01"
    assert d.dipendenti == 42 and d.fatturato == Decimal("1500000") and d.anno_dati == 2023
    assert d.avvisi == []


def test_registro_imprese_produzione_e_livello_base():
    richieste: list[httpx.Request] = []
    valori = {**VALORI, "ambiente": "produzione", "livello": "start"}
    ri.cerca(valori, "12485671007", _client(200, ESEMPIO_ADVANCED, richieste))
    assert str(richieste[0].url) == "https://company.openapi.com/IT-start/12485671007"


@pytest.mark.parametrize(
    ("stato", "frase"),
    [
        (401, "Token non valido"),
        (403, "scope"),
        (402, "Credito Openapi esaurito"),
        (404, "sandbox rispondono solo"),
        (406, "non valido"),
        (500, "risposto con un errore"),
    ],
)
def test_registro_imprese_errori(stato, frase):
    with pytest.raises(ri.ErroreRegistro, match=frase):
        ri.cerca(VALORI, "12485671007", _client(stato))


def test_registro_imprese_validazioni():
    with pytest.raises(ri.ErroreRegistro, match="Manca il token"):
        ri.cerca({}, "12485671007")
    with pytest.raises(ri.ErroreRegistro, match="partita IVA"):
        ri.cerca(VALORI, "123")


def test_registro_imprese_azienda_cessata():
    record = {**ESEMPIO_ADVANCED["data"][0], "activityStatus": "CESSATA", "endDate": "2024-01-01"}
    d = ri.interpreta(record)
    assert any("CESSATA" in a for a in d.avvisi)


def test_regione_da_testo():
    assert ri.regione_da_testo("LAZIO") == "Lazio"
    assert ri.regione_da_testo("TRENTINO-ALTO ADIGE") == "Trentino-Alto Adige/Südtirol"
    assert ri.regione_da_testo("VALLE D'AOSTA") == "Valle d'Aosta/Vallée d'Aoste"
    assert ri.regione_da_testo("EMILIA ROMAGNA") == "Emilia-Romagna"
    assert ri.regione_da_testo("Atlantide") is None


def test_ogni_integrazione_ha_la_guida():
    for tipo in INTEGRAZIONI.values():
        assert len(tipo.guida) >= 3, tipo.codice
        for _, url in tipo.link_guida:
            assert url.startswith("https://")
    assert INTEGRAZIONI["registro_imprese"].attiva_predefinita is False


# --- TED ---------------------------------------------------------------------------------


def test_ted_interpreta_campione_reale():
    campione = json.loads((FIXTURES / "ted_campione.json").read_text(encoding="utf-8"))
    righe = [ted.interpreta(n) for n in campione]
    lazio = righe[0]
    assert lazio["id_esterno"] == campione[0]["publication-number"]
    assert "Lazio" in lazio["regioni"]
    assert lazio["scadenza"] is not None
    assert lazio["link"].startswith("https://ted.europa.eu/")
    assert not lazio["titolo"].startswith("Italia")
    assert all(len(d) == 2 for d in lazio["divisioni_cpv"])
    assert righe[2]["regioni"] is None  # luogo non italiano o generico


def test_ted_scarica_pagine_con_token():
    pagine = [
        {"notices": [{"publication-number": "1-2026"}], "iterationNextToken": "t1"},
        {"notices": [{"publication-number": "2-2026"}], "iterationNextToken": "t2"},
        {"notices": [], "iterationNextToken": None},
    ]
    corpi: list[dict] = []

    def gestore(req: httpx.Request) -> httpx.Response:
        corpi.append(json.loads(req.content))
        return httpx.Response(200, json=pagine[len(corpi) - 1])

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        avvisi = ted.scarica(c, pausa=0)
    assert [a["publication-number"] for a in avvisi] == ["1-2026", "2-2026"]
    assert "iterationNextToken" not in corpi[0]
    assert corpi[1]["iterationNextToken"] == "t1"
    assert corpi[0]["paginationMode"] == "ITERATION" and corpi[0]["limit"] == 250


# --- Posta -------------------------------------------------------------------------------


def test_composizione_email():
    msg = posta.componi(
        {"mittente": "studio@esempio.it", "nome_mittente": "Studio Rossi"},
        "cliente@esempio.it",
        "Documenti",
        "Gentile cliente,\nservono i documenti.",
        rispondi_a="consulente@esempio.it",
    )
    assert msg["From"] == "Studio Rossi <studio@esempio.it>"
    assert msg["Reply-To"] == "consulente@esempio.it"
    assert "servono i documenti" in msg.get_content()
    with pytest.raises(posta.ErroreInvio):
        posta.invia({"host": "x", "utente": "u", "password": "p"}, "senza-chiocciola", "a", "b")


# --- Allegati ----------------------------------------------------------------------------


def test_allegati(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cartella_allegati", str(tmp_path))
    monkeypatch.setattr(get_settings(), "allegato_max_mb", 1)
    relativo, mime, dim, sha = allegati.salva(io.BytesIO(b"%PDF-1.4 prova"), "Visura camerale.PDF")
    assert mime == "application/pdf" and dim == 14 and len(sha) == 64
    assert relativo.endswith(".pdf") and "Visura" not in relativo
    assert allegati.percorso_assoluto(relativo).read_bytes() == b"%PDF-1.4 prova"
    with pytest.raises(allegati.ErroreAllegato, match="non ammesso"):
        allegati.salva(io.BytesIO(b"x"), "virus.exe")
    with pytest.raises(allegati.ErroreAllegato, match="troppo grande"):
        allegati.salva(io.BytesIO(b"x" * (1024 * 1024 + 1)), "grande.pdf")
    with pytest.raises(allegati.ErroreAllegato, match="vuoto"):
        allegati.salva(io.BytesIO(b""), "vuoto.pdf")
    with pytest.raises(allegati.ErroreAllegato):
        allegati.percorso_assoluto("../../etc/passwd")
    # nessun file orfano lasciato dai tentativi falliti
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


# --- Prefattibilità ----------------------------------------------------------------------


def _azienda(regione="Lazio", dimensione="piccola", ateco="62.01", stato="ATTIVA"):
    return SimpleNamespace(
        sede_legale=SimpleNamespace(regione=regione) if regione else None,
        dimensione_manuale=dimensione,
        ateco_primario=SimpleNamespace(codice=ateco) if ateco else None,
        stato_attivita=stato,
    )


def _bando(**req):
    base = {
        "territorio": "regionale",
        "regioni": ["Lazio"],
        "dimensioni": ["micro", "piccola", "media"],
        "ateco_ammessi": ["62"],
        "regime": "de_minimis",
    }
    base.update(req)
    return SimpleNamespace(
        stato="aperto",
        data_chiusura=date(2026, 12, 31),
        data_apertura=None,
        requisiti=SimpleNamespace(**base),
    )


def _esiti(voci):
    return {v["requisito"]: v["esito"] for v in voci}


def test_prefattibilita_voci_automatiche():
    oggi = date(2026, 11, 18)
    voci = voci_automatiche(_azienda(), _bando(), oggi)
    e = _esiti(voci)
    assert e["Territorio"] == "ok" and e["Dimensione d'impresa"] == "ok"
    assert e["Codice ATECO"] == "ok" and e["Plafond de minimis"] == "da_verificare"
    assert esito_suggerito(voci) == "fattibile_con_riserva"

    e = _esiti(voci_automatiche(_azienda(regione="Toscana", dimensione="grande"), _bando(), oggi))
    assert e["Territorio"] == "ko" and e["Dimensione d'impresa"] == "ko"

    e = _esiti(voci_automatiche(_azienda(ateco="47.11", stato="CESSATA"), _bando(), oggi))
    assert e["Codice ATECO"] == "ko" and e["Impresa attiva"] == "ko"

    e = _esiti(voci_automatiche(_azienda(regione=None, ateco=None), _bando(), oggi))
    assert e["Territorio"] == "da_verificare" and e["Codice ATECO"] == "da_verificare"

    voci = voci_automatiche(_azienda(), _bando(territorio="nazionale", regime="esenzione"), oggi)
    assert esito_suggerito(voci) == "fattibile"
    assert voci_automatiche(_azienda(), None, oggi)[0]["esito"] == "da_verificare"
