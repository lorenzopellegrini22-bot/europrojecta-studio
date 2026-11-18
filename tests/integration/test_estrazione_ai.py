"""Fase 3 su database reale, con un client OpenAI finto (nessuna chiamata di rete)."""

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extraction import servizio
from app.extraction.applica import scrivi, valori_da_fonte
from app.models import Bando, Fonte, Integrazione
from app.models.ai import BandoOverride, ImpostazioniAI, UsoLLM
from app.sources.base import RequisitiFonte
from tests.integration.conftest import csrf

pytestmark = pytest.mark.integration

DESCRIZIONE = (
    "Possono partecipare le micro, piccole e medie imprese con sede in Toscana. Il contributo "
    "a fondo perduto è pari al 50% delle spese ammissibili per l'acquisto di software e "
    "servizi cloud, fino a un massimo di euro 20.000. Gli aiuti sono concessi in regime "
    "«de minimis». Le domande si presentano a sportello dal 1° dicembre 2026 al 31 gennaio 2027."
)


def _c(valore, citazione=None, confidenza="alta"):
    return {
        "valore": valore,
        "confidenza": confidenza,
        "citazione": citazione,
        "da_verificare": valore is None,
    }


RISPOSTA = {
    "ente": _c(None),
    "territorio": _c("regionale", "con sede in Toscana"),
    "regioni": _c(["Toscana"], "con sede in Toscana"),
    "province": _c(None),
    "comuni": _c(None),
    "dimensioni": _c(["micro", "piccola", "media"], "micro, piccole e medie imprese"),
    "ateco_ammessi": _c(None),
    "ateco_esclusi": _c(None),
    "forme_giuridiche": _c(None),
    "anzianita_min_mesi": _c(None),
    "anzianita_max_mesi": _c(None),
    "categorie_spesa": _c(["digitale e software"], "per l'acquisto di software e servizi cloud"),
    "forma_agevolazione": _c(["Contributo/Fondo perduto"], "Il contributo a fondo perduto"),
    "percentuale": _c(50, "pari al 50% delle spese ammissibili"),
    "importo_min": _c(None),
    "importo_max": _c(20000, "fino a un massimo di euro 20.000"),
    "dotazione": _c(None),
    "regime": _c("de_minimis", "concessi in regime «de minimis»"),
    "modalita": _c("sportello", "Le domande si presentano a sportello"),
    "data_apertura": _c("2026-12-01", "dal 1° dicembre 2026"),
    "data_chiusura": _c("2027-01-31", "al 31 gennaio 2027"),
    "altri_requisiti": _c(None),
    "sintesi": _c("Contributo a fondo perduto del 50% per software e cloud delle PMI toscane."),
}


class ClientFinto:
    def __init__(self, risposta=RISPOSTA):
        self.chiamate = []
        self.risposta = risposta
        self.responses = self

    def create(self, **kw):
        self.chiamate.append(kw)
        return SimpleNamespace(
            output_text=json.dumps(self.risposta),
            usage=SimpleNamespace(input_tokens=2000, output_tokens=500),
        )


@pytest.fixture
def ai_pronta(db, crea_utente):
    """OpenAI collegato (chiave finta) e impostazioni AI con prezzi e budget."""
    from app.integrazioni.servizio import salva

    db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
    db.query(ImpostazioniAI).delete()
    db.query(UsoLLM).delete()
    db.commit()
    utente = crea_utente()
    salva(db, "openai", {"api_key": "sk-finta"}, set(), utente)
    db.add(
        ImpostazioniAI(
            id=1,
            modello_estrazione="modello-prova",
            budget_mensile_eur=Decimal("10"),
            prezzi={"modello-prova": {"ingresso": "2", "uscita": "8"}},
            estrazioni_per_notte=5,
        )
    )
    db.commit()
    yield
    db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
    db.query(ImpostazioniAI).delete()
    db.commit()


def _bando_senza_date(db) -> Bando:
    fonte = db.scalar(select(Fonte).where(Fonte.codice == "prova_ai"))
    if fonte is None:
        fonte = Fonte(codice="prova_ai", nome="Prova AI", url="https://esempio.test")
        db.add(fonte)
        db.flush()
    b = Bando(
        fonte_id=fonte.id,
        id_esterno=uuid.uuid4().hex,
        titolo="Bando Digitalizzazione PMI",
        ente="Regione Toscana",
        descrizione=DESCRIZIONE,
        stato="aperto",
        stato_fonte="aperto",
        raw={},
        hash_contenuto="x" * 64,
    )
    rf = RequisitiFonte(territorio="regionale", regioni=["Toscana"])
    rf.da_verificare = ["date", "importi", "regime", "modalita", "categorie_spesa"]
    scrivi(b, valori_da_fonte(rf), None, None)
    db.add(b)
    db.commit()
    return b


def test_estrazione_completa(client, crea_utente, accedi, db, ai_pronta):
    utente = crea_utente()
    accedi(utente)
    b = _bando_senza_date(db)
    assert "regime" in b.requisiti.da_verificare
    finto = ClientFinto()
    esito = servizio.esegui(db, client=finto, oggi=date(2026, 11, 18))
    assert "bandi strutturati" in esito
    chiamata = finto.chiamate[0]
    assert chiamata["model"] == "modello-prova"
    assert chiamata["text"]["format"]["strict"] is True
    assert DESCRIZIONE in chiamata["input"]

    db.expire_all()
    b = db.get(Bando, b.id)
    req = b.requisiti
    assert req.regime == "de_minimis" and req.provenienza["regime"] == "llm"
    assert req.modalita == "sportello" and req.categorie_spesa == ["digitale e software"]
    assert req.percentuale == Decimal("50") and req.importo_max == Decimal("20000")
    assert req.regioni == ["Toscana"] and req.provenienza["regioni"] == "fonte"
    assert req.sintesi.startswith("Contributo a fondo perduto")
    # date dal testo: il bando passa da "aperto dichiarato" a "in arrivo"
    assert b.data_apertura == date(2026, 12, 1) and b.data_chiusura == date(2027, 1, 31)
    assert b.stato == "in_arrivo"
    assert "date" not in (req.da_verificare or [])
    # costo: 2000 × 2 €/M + 500 × 8 €/M = 0,008 €
    uso = db.scalar(select(UsoLLM).where(UsoLLM.operazione == "estrazione"))
    assert uso.costo_stimato == Decimal("0.0080")
    # una seconda esecuzione non rifà lo stesso bando (input invariato)
    finto2 = ClientFinto()
    servizio.esegui(db, client=finto2, oggi=date(2026, 11, 18))
    assert not [c for c in finto2.chiamate if DESCRIZIONE in c["input"]]

    # Revisione: correzione manuale del regime
    pagina = client.get(f"/revisione/{b.id}").text
    assert "<mark>" in pagina and "de_minimis" in pagina
    token = csrf(client, f"/revisione/{b.id}")
    r = client.post(
        f"/revisione/{b.id}",
        data={"csrf_token": token, "campo": "regime", "azione": "correggi", "valore": "esenzione"},
    )
    assert r.status_code == 303
    db.expire_all()
    b = db.get(Bando, b.id)
    assert b.requisiti.regime == "esenzione" and b.requisiti.provenienza["regime"] == "manuale"
    assert db.scalar(select(BandoOverride).where(BandoOverride.bando_id == b.id))

    # Una nuova raccolta della fonte non cancella AI e correzioni
    from app.sources import runner

    rf = RequisitiFonte(territorio="regionale", regioni=["Toscana"])
    rf.da_verificare = ["date", "importi", "regime", "modalita", "categorie_spesa"]
    from app.extraction.applica import carica_ai_e_manuali

    ai, manuali = carica_ai_e_manuali(db, [b.id])
    runner._AI.update(ai)
    runner._MANUALI.update(manuali)
    runner.scrivi_requisiti(b, rf)
    db.commit()
    db.expire_all()
    b = db.get(Bando, b.id)
    assert b.requisiti.regime == "esenzione" and b.requisiti.modalita == "sportello"
    assert db.get(Bando, b.id).requisiti.sintesi

    # Pagine
    assert client.get("/revisione").status_code == 200
    assert "Contributo a fondo perduto del 50%" in client.get(f"/bandi/{b.id}").text


def test_budget_esaurito_ferma_le_chiamate(db, ai_pronta):
    imp = db.get(ImpostazioniAI, 1)
    imp.budget_mensile_eur = Decimal("0.001")
    db.add(
        UsoLLM(
            operazione="estrazione", modello="modello-prova", costo_stimato=Decimal("1"), esito="ok"
        )
    )
    db.commit()
    _bando_senza_date(db)
    finto = ClientFinto()
    servizio.esegui(db, client=finto, oggi=date.today())
    assert finto.chiamate == []
    db.expire_all()
    imp = db.get(ImpostazioniAI, 1)
    assert imp.sospesa_fino_al and imp.sospesa_fino_al > date.today()
    assert "sospesa" in servizio.esegui(db, client=finto, oggi=date.today())


def test_senza_configurazione_non_parte(db):
    db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
    db.commit()
    assert "non è collegato" in servizio.esegui(db, client=ClientFinto())


def test_pagina_impostazioni_ai(client, crea_utente, accedi, db, ai_pronta):
    accedi(crea_utente())
    pagina = client.get("/impostazioni/ai").text
    assert "Spesa stimata del mese" in pagina and "modello-prova" in pagina
    assert "developers.openai.com/api/docs/guides/spend-limits" in pagina
    token = csrf(client, "/impostazioni/ai")
    r = client.post(
        "/impostazioni/ai",
        data={
            "csrf_token": token,
            "modello_estrazione": "altro-modello",
            "budget_mensile_eur": "25,50",
            "estrazioni_per_notte": "100",
            "prezzo_modello_0": "altro-modello",
            "prezzo_ingresso_0": "1,5",
            "prezzo_uscita_0": "6",
        },
    )
    assert r.status_code == 303
    db.expire_all()
    imp = db.get(ImpostazioniAI, 1)
    assert imp.modello_estrazione == "altro-modello" and imp.budget_mensile_eur == Decimal("25.50")
    assert imp.prezzi == {"altro-modello": {"ingresso": "1.5", "uscita": "6"}}
    assert imp.estrazioni_per_notte == 100


def test_pdf_dei_bandi_letti_dall_ai(
    client, crea_utente, accedi, db, ai_pronta, tmp_path, monkeypatch
):
    import httpx

    from app.config import get_settings
    from app.documenti import servizio as documenti
    from app.models import DocumentoBando
    from tests.unit.test_documenti import TESTO_BANDO, pdf_con_testo

    monkeypatch.setattr(get_settings(), "cartella_allegati", str(tmp_path / "allegati"))
    monkeypatch.setattr(documenti.http, "INTERVALLO_MINIMO_SECONDI", 0)
    accedi(crea_utente())
    url = f"https://ente.test/{uuid.uuid4().hex}/DD_Bando.pdf"
    bandi = []
    for _ in range(2):
        b = _bando_senza_date(db)
        b.raw = {"documenti_pdf": [{"titolo": "Bando", "url": url}]}
        bandi.append(b)
    db.commit()
    # prima estrazione senza PDF
    servizio.esegui(db, client=ClientFinto(), oggi=date(2026, 11, 18))

    richieste = []

    def gestore(req: httpx.Request) -> httpx.Response:
        richieste.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=pdf_con_testo(TESTO_BANDO))

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        esito = documenti.esegui(db, client=c, limite=50)
    assert "letti" in esito
    assert sum(1 for r in richieste if r == url) == 1  # stesso PDF per due bandi: uno scarico
    docs = list(db.scalars(select(DocumentoBando).where(DocumentoBando.url == url)))
    assert len(docs) == 2 and all("de minimis" in d.testo_estratto for d in docs)

    # il testo per l'AI contiene il PDF, quindi il bando viene rianalizzato
    testo = servizio.testo_bando(bandi[0], db)
    assert "DOCUMENTO PDF: Bando" in testo and "Spese ammissibili" in testo
    finto = ClientFinto()
    servizio.esegui(db, client=finto, oggi=date(2026, 11, 18))
    assert any("DOCUMENTO PDF" in c["input"] for c in finto.chiamate)

    pagina = client.get(f"/bandi/{bandi[0].id}").text
    assert "Documenti PDF" in pagina and "1 pagine lette" in pagina
