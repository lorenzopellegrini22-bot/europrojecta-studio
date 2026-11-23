"""Fonte europea (portale Funding & Tenders) e abbinamento dei bandi UE.

La fixture contiene 5 topic reali scaricati il 23/11/2026 (testi della Commissione europea,
riutilizzo consentito citando la fonte)."""

import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace as N

from app.opportunita.abbinamento import (
    PUNTEGGIO_MINIMO,
    ambito_bando,
    valuta_bando,
    valuta_bando_con_motivo,
)
from app.sources import ue

FIXTURE = Path(__file__).parents[1] / "fixtures" / "ue_funding_tenders.json"
OGGI = date(2026, 11, 23)


def _record(tmp_path):
    copia = tmp_path / "ue.json"
    shutil.copy(FIXTURE, copia)
    return {r.id_esterno: r for r in ue.FONTE.interpreta(copia, oggi=OGGI)}


def test_estrazione_dei_topic(tmp_path):
    recs = _record(tmp_path)
    neb = recs["ue-HORIZON-NEB-2026-01-PARTICIPATION-02"]
    assert neb.titolo == "Innovative approaches for the spatial design of neighbourhoods"
    assert neb.ente == "Commissione europea – Horizon Europe"
    assert neb.link_ufficiale.endswith("/topic-details/HORIZON-NEB-2026-01-PARTICIPATION-02")
    assert neb.data_chiusura == date(2026, 12, 1) and neb.stato_fonte == "aperto"
    assert neb.requisiti.territorio == "nazionale"
    assert neb.requisiti.importo_min == 3500000 and neb.requisiti.importo_max == 6300000
    d = neb.descrizione
    assert "Contributo UE per progetto: da 3.500.000 € a 6.300.000 €" in d
    assert "Expected Outcome:" in d and "CONDIZIONI E CRITERI DI VALUTAZIONE" in d
    assert "Award criteria, scoring and thresholds" in d
    # frasi intere: niente a capo a ogni link o grassetto
    assert "described in Annex A and Annex E of the Horizon Europe" in d
    # beneficiari ricavati: quartieri → enti; programma di ricerca → università
    assert "Ente Pubblico" in neb.requisiti.tipologie_soggetto
    assert recs["ue-HORIZON-MSCA-2026-DN-01-01"].requisiti.tipologie_soggetto == [
        "Università/Ente di Ricerca"
    ]
    cerv = recs["ue-CERV-2026-CITIZENS-REM-HOLOCAUSTJEW"].requisiti.tipologie_soggetto
    assert cerv == ["Ente Pubblico"]
    assert (
        "Impresa"
        in recs["ue-DIGITAL-ECCC-2027-DEPLOY-CYBER-11-AI4SME"].requisiti.tipologie_soggetto
    )


def test_scadenze_passate_e_doppioni(tmp_path):
    import json

    dati = json.loads(FIXTURE.read_text(encoding="utf-8"))
    primo = dati["risultati"][0]
    scaduto = json.loads(json.dumps(primo))
    scaduto["metadata"]["identifier"] = ["SCADUTO-1"]
    scaduto["metadata"]["deadlineDate"] = ["2023-01-01T00:00:00.000+0000"]
    dati["risultati"] += [primo, scaduto]  # lo stesso topic due volte e uno scaduto
    p = tmp_path / "ue.json"
    p.write_text(json.dumps(dati), encoding="utf-8")
    ids = [r.id_esterno for r in ue.FONTE.interpreta(p, oggi=OGGI)]
    assert len(ids) == len(set(ids)) == 5 and "ue-SCADUTO-1" not in ids


def test_bandi_solo_per_la_societa_civile():
    assert ue.tipologie("CERV-2026-CITIZENS-VALUES", "Call to intermediaries for civil "
                        "society organisations", "") != ["Ente Pubblico"]  # fmt: skip


def _bando(rec):
    return N(titolo=rec.titolo, descrizione=rec.descrizione, id_esterno=rec.id_esterno,
             raw=rec.raw, stato="aperto", ente=rec.ente,
             requisiti=N(**{**rec.requisiti.__dict__, "regime": None, "forme_giuridiche": None,
                            "categorie_spesa": None, "finalita": None, "ateco_esclusi": None,
                            "anzianita_max_mesi": None, "anzianita_min_mesi": None,
                            "regime_aiuto": None}))  # fmt: skip


def _comune(abitanti=67000, ambiti=frozenset({"ue", "nazionali", "locali"})):
    s = N(tipo="legale", comune_testo="Viterbo", provincia="VT", regione="Lazio",
          codice_istat=None)  # fmt: skip
    return N(ragione_sociale="Comune di Viterbo", tipo_soggetto="ente_pubblico",
             abitanti=abitanti, sedi=[s], sede_legale=s, ambiti=set(ambiti))  # fmt: skip


def _impresa(ambiti=frozenset({"ue", "nazionali", "locali"})):
    s = N(tipo="legale", comune_testo="Viterbo", provincia="VT", regione="Lazio",
          codice_istat=None)  # fmt: skip
    return N(ragione_sociale="Officine Tuscia S.r.l.", tipo_soggetto="impresa", abitanti=None,
             forma_giuridica="S.r.l.", sedi=[s], sede_legale=s, ambiti=set(ambiti),
             ateco_primario=N(codice="62.01", versione="2007"), dimensione_manuale="piccola",
             data_costituzione=None, plafond_de_minimis_manuale=None, dipendenti=18,
             fatturato=None, totale_attivo=None)  # fmt: skip


def test_abbinamento_ue_per_comuni_e_imprese(tmp_path):
    from app.opportunita.abbinamento import Contesto

    recs = {k: _bando(r) for k, r in _record(tmp_path).items()}
    cerv = recs["ue-CERV-2026-CITIZENS-REM-HOLOCAUSTJEW"]
    neb = recs["ue-HORIZON-NEB-2026-01-PARTICIPATION-02"]
    ricerca = recs["ue-HORIZON-MSCA-2026-DN-01-01"]
    cyber = recs["ue-DIGITAL-ECCC-2027-DEPLOY-CYBER-11-AI4SME"]
    assert ambito_bando(cerv) == "ue"

    # comune: bandi per gli enti sì, ricerca pura no; senza la casella UE niente
    es = valuta_bando(_comune(), cerv)
    assert es.punteggio >= PUNTEGGIO_MINIMO
    assert "Bando europeo: aperto ai soggetti di tutti i Paesi UE" in es.soddisfatti
    assert valuta_bando(_comune(), neb).punteggio >= PUNTEGGIO_MINIMO
    assert valuta_bando(_comune(), ricerca) is None
    # piccolo comune: Horizon sotto la soglia
    assert valuta_bando(_comune(abitanti=1300), neb).punteggio < PUNTEGGIO_MINIMO
    esito, motivo = valuta_bando_con_motivo(_comune(ambiti={"nazionali"}), cerv)
    assert esito is None and "europei non attiva" in motivo

    # impresa: senza un progetto collegato i bandi UE restano sotto la soglia
    assert valuta_bando(_impresa(), cyber).punteggio < PUNTEGGIO_MINIMO
    digitale = N(id=1, titolo="Cybersicurezza", descrizione="", attivo=True,
                 categorie=["digitale e software"])  # fmt: skip
    es = valuta_bando(_impresa(), cyber, Contesto(progetti=[digitale]))
    assert es.punteggio >= PUNTEGGIO_MINIMO and es.progetto_id == 1
    # Horizon solo con un progetto di ricerca e sviluppo
    energia = N(id=2, titolo="Fotovoltaico", descrizione="", attivo=True,
                categorie=["energia e ambiente", "immobili e opere"])  # fmt: skip
    assert valuta_bando(_impresa(), neb, Contesto(progetti=[energia])) is None or (
        valuta_bando(_impresa(), neb, Contesto(progetti=[energia])).punteggio < PUNTEGGIO_MINIMO
    )
