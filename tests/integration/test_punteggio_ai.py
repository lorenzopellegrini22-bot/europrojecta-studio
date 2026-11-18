"""Fase 4 su database reale, con un client OpenAI finto (embeddings + valutazione)."""

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Appalto, Azienda, AziendaAteco, BandoRequisiti, Sede
from app.models.ai import Embedding, ImpostazioniAI, UsoLLM
from app.models.opportunita import Opportunita
from app.opportunita import valutazione
from app.opportunita.abbinamento import abbina, da_notificare
from tests.integration.conftest import csrf
from tests.integration.test_estrazione_ai import ai_pronta  # noqa: F401  (fixture)
from tests.integration.test_pratiche import _bando, _cliente

pytestmark = pytest.mark.integration


class ClientFinto:
    """Embeddings tutti uguali (similarità 1) e punteggio 85 per le gare, 30 per i bandi."""

    def __init__(self):
        self.valutazioni = []
        self.embedding_input = []
        self.responses = SimpleNamespace(create=self._valuta)
        self.embeddings = SimpleNamespace(create=self._embeddings)

    def _valuta(self, **kw):
        self.valutazioni.append(kw)
        gara = "GARA D'APPALTO" in kw["input"]
        dati = {
            "punteggio": 85 if gara else 30,
            "motivazione": "Categoria SOA posseduta e territorio coerente."
            if gara
            else "Settore non coerente con l'attività.",
            "soddisfatti": ["OG6 classifica III"] if gara else [],
            "da_verificare": ["requisiti di fatturato"],
            "mancanti": [] if gara else ["ATECO non ammesso"],
        }
        return SimpleNamespace(
            output_text=json.dumps(dati),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )

    def _embeddings(self, model, input):
        self.embedding_input += input
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0, 0.5]) for _ in input],
            usage=SimpleNamespace(prompt_tokens=50 * len(input)),
        )


def test_profilo_anonimo_senza_dati_identificativi():
    a = Azienda(
        ragione_sociale="Costruzioni Segrete S.r.l.",
        partita_iva="01234567890",
        codice_fiscale="01234567890",
        email="info@segrete.it",
        pec="segrete@pec.it",
        note="cliente storico di Mario",
        forma_giuridica="SRL",
        dimensione_manuale="piccola",
        dipendenti=12,
        fatturato=Decimal("1500000"),
        soa_possedute=[{"codice": "OG6", "classifica": "III"}],
        cpv_interesse=["45"],
    )
    a.sedi = [Sede(tipo="legale", regione="Abruzzo", provincia="CH", indirizzo="Via Roma 1")]
    a.ateco = [AziendaAteco(codice="42.21", descrizione="Condotte idriche", primario=True)]
    testo = valutazione.profilo_anonimo(a)
    for vietato in ("Segrete", "01234567890", "info@", "pec", "Mario", "Via Roma"):
        assert vietato not in testo
    for atteso in ("piccola", "Abruzzo", "CH", "42.21", "OG6 III", "dipendenti: 12", "1.500.000"):
        assert atteso in testo


def test_punteggio_ai_completo(client, crea_utente, accedi, db, ai_pronta):  # noqa: F811
    utente = crea_utente()
    accedi(utente)
    imp = db.get(ImpostazioniAI, 1)
    imp.modello_scoring = "modello-prova"
    imp.modello_embeddings = "emb-prova"
    imp.prezzi = {
        "modello-prova": {"ingresso": "2", "uscita": "8"},
        "emb-prova": {"ingresso": "0.02", "uscita": "0"},
    }
    db.commit()
    azienda = _cliente(
        client,
        db,
        avvisi_attivi="1",
        regione="Abruzzo",
        dimensione_manuale="piccola",
        soa_testo="OG6 III",
    )
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
    db.add(
        Appalto(
            fonte="ted",
            id_esterno=f"prova-{uuid.uuid4().hex[:8]}",
            titolo="Rete idrica Vasto",
            stazione_appaltante="Comune di prova",
            regioni=["Abruzzo"],
            categorie_soa=[{"codice": "OG6", "tipo": "prevalente"}],
            codici_soa=["OG6"],
            natura=["lavori"],
            importo_stimato=Decimal("800000"),
            scadenza=date.today() + timedelta(days=20),
            raw={},
        )
    )
    db.commit()
    abbina(db)

    finto = ClientFinto()
    esito = valutazione.esegui(db, finto)
    assert "valutate" in esito
    # al modello non arriva mai il nome del cliente né la partita IVA
    inviati = " ".join(v["input"] for v in finto.valutazioni) + " ".join(finto.embedding_input)
    assert azienda.ragione_sociale not in inviati and azienda.partita_iva not in inviati
    assert all(v["text"]["format"]["strict"] for v in finto.valutazioni)

    ops = {
        ("bando" if o.bando_id else "appalto"): o
        for o in db.scalars(select(Opportunita).where(Opportunita.azienda_id == azienda.id))
    }
    gara, b = ops["appalto"], ops["bando"]
    assert gara.punteggio_ai == 85 and gara.punteggio == 85 and gara.punteggio_regole is not None
    assert b.punteggio_ai == 30 and b.punteggio == 30
    assert gara.similarita == pytest.approx(1.0)
    assert gara.requisiti_ai["soddisfatti"] == ["OG6 classifica III"]
    operazioni = {u.operazione for u in db.scalars(select(UsoLLM))}
    assert {"embedding", "scoring"} <= operazioni
    assert db.scalar(
        select(Embedding).where(Embedding.entita == "azienda", Embedding.entita_id == azienda.id)
    )

    # Seconda esecuzione: niente cambia, nessuna nuova chiamata (cache su hash)
    finto2 = ClientFinto()
    valutazione.esegui(db, finto2)
    assert finto2.valutazioni == [] and finto2.embedding_input == []

    # Un nuovo abbinamento a regole non cancella il punteggio AI
    abbina(db)
    db.expire_all()
    assert db.get(Opportunita, gara.id).punteggio == 85

    # Email del mattino: sotto la soglia AI (60) il bando non entra
    nuove = {o.id for o in da_notificare(db, 60)}
    assert gara.id in nuove and b.id not in nuove

    # Pagina: punteggio AI, motivazione, pulsanti di feedback
    pagina = client.get(f"/opportunita?cliente={azienda.id}").text
    assert "Categoria SOA posseduta" in pagina and "Non pertinente" in pagina

    # Feedback
    token = csrf(client, "/opportunita")
    client.post(
        f"/opportunita/{b.id}/feedback", data={"csrf_token": token, "feedback": "non_pertinente"}
    )
    client.post(f"/opportunita/{gara.id}/feedback", data={"csrf_token": token, "feedback": "utile"})
    db.expire_all()
    assert db.get(Opportunita, b.id).stato == "scartata"
    assert db.get(Opportunita, b.id).feedback_utente_id == utente.id
    assert db.get(Opportunita, gara.id).stato == "interessante"
    # Una non pertinente non torna con il nuovo abbinamento né si rivaluta
    abbina(db)
    db.expire_all()
    assert db.get(Opportunita, b.id).stato == "scartata"

    precisione = {f["etichetta"]: f for f in valutazione.precisione(db)}
    assert precisione["80-100 adatta"]["pertinenti"] >= 1
    assert precisione["0-39 non adatta"]["totale"] >= 1
    assert "Precisione del punteggio" in client.get("/statistiche").text

    # Impostazioni
    token = csrf(client, "/impostazioni/ai")
    r = client.post(
        "/impostazioni/ai",
        data={
            "csrf_token": token,
            "modello_scoring": "modello-prova",
            "modello_embeddings": "emb-prova",
            "valutazioni_per_notte": "20",
            "valutazioni_per_cliente": "5",
            "soglia_similarita": "0,35",
            "soglia_notifica": "70",
            "estrazioni_per_notte": "5",
        },
    )
    assert r.status_code == 303
    db.expire_all()
    imp = db.get(ImpostazioniAI, 1)
    assert (imp.valutazioni_per_notte, imp.valutazioni_per_cliente, imp.soglia_notifica) == (
        20,
        5,
        70,
    )
    assert imp.soglia_similarita == pytest.approx(0.35)


def test_senza_openai_non_valuta(db):
    from app.models import Integrazione

    db.query(Integrazione).filter(Integrazione.codice == "openai").delete()
    db.commit()
    assert "non eseguita" in valutazione.esegui(db, ClientFinto())
