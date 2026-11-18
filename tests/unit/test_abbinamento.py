"""Abbinamento clienti ↔ bandi e appalti: regole, senza database."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace as N

from app.opportunita.abbinamento import (
    _classifica_copre,
    regioni_cliente,
    valuta_appalto,
    valuta_bando,
)


def _cliente(**kw):
    base = {
        "sede_legale": N(regione="Lazio", comune_testo="Frosinone"),
        "dimensione_manuale": "piccola",
        "ateco_primario": N(codice="41.20"),
        "soa_possedute": [{"codice": "OG1", "classifica": "II"}],
        "regioni_interesse": ["Abruzzo"],
        "cpv_interesse": ["45"],
    }
    base.update(kw)
    return N(**base)


def _bando(titolo="Contributi alle imprese", stato="aperto", **req):
    r = {
        "tipologie_soggetto": ["Impresa"],
        "territorio": "regionale",
        "regioni": ["Lazio"],
        "comuni": None,
        "dimensioni": ["micro", "piccola", "media"],
        "ateco_ammessi": None,
    }
    r.update(req)
    return N(titolo=titolo, stato=stato, requisiti=N(**r))


def test_bandi_esclusioni_e_punteggio():
    c = _cliente()
    base = valuta_bando(c, _bando())
    assert base and "Bando per la regione della sede (Lazio)" in base.soddisfatti
    assert valuta_bando(c, _bando(regioni=["Toscana"])) is None
    assert valuta_bando(c, _bando(dimensioni=["grande"])) is None
    assert valuta_bando(c, _bando(tipologie_soggetto=["Ente Pubblico", "Cittadino"])) is None
    assert valuta_bando(c, _bando(ateco_ammessi=["62"])) is None
    con_ateco = valuta_bando(c, _bando(ateco_ammessi=["41", "43"]))
    assert con_ateco.punteggio > base.punteggio
    nazionale = valuta_bando(c, _bando(territorio="nazionale", regioni=None))
    assert nazionale and "Bando nazionale" in nazionale.soddisfatti


def test_bandi_comunali_e_di_settore():
    c = _cliente()
    assert valuta_bando(c, _bando(territorio="comunale", comuni=["Pistoia"])) is None
    stesso = valuta_bando(c, _bando(territorio="comunale", comuni=["FROSINONE"]))
    assert stesso and stesso.punteggio >= 70
    # bando per la cultura: escluso per un'impresa edile, valido per un'impresa culturale
    assert valuta_bando(c, _bando(titolo="Atelier dell'Immaginario - cinema")) is None
    culturale = _cliente(ateco_primario=N(codice="59.11"))
    assert valuta_bando(culturale, _bando(titolo="Bando cinema e audiovisivo")) is not None
    senza_ateco = valuta_bando(_cliente(ateco_primario=None), _bando(titolo="Bando turismo"))
    assert senza_ateco and any("turismo" in x for x in senza_ateco.da_verificare)


def test_classifiche_soa():
    assert _classifica_copre("II", Decimal("600000")) is True  # 516.000 + un quinto
    assert _classifica_copre("II", Decimal("700000")) is False
    assert _classifica_copre("VIII", Decimal("99000000")) is True
    assert _classifica_copre(None, Decimal("1")) is None


def _appalto(**kw):
    base = {
        "regioni": ["Lazio"],
        "categorie_soa": [
            {"codice": "OG1", "tipo": "prevalente", "importo": "400000"},
            {"codice": "OS30", "tipo": "scorporabile", "importo": "50000"},
        ],
        "importo_stimato": Decimal("450000"),
        "natura": ["lavori"],
        "cpv": ["45210000"],
        "divisioni_cpv": ["45"],
        "scadenza": date.today() + timedelta(days=20),
    }
    base.update(kw)
    return N(**base)


def test_appalti():
    c = _cliente()
    reg = regioni_cliente(c)
    assert reg == {"Lazio", "Abruzzo"}
    ok = valuta_appalto(c, _appalto(), reg)
    assert any("classifica II sufficiente" in s for s in ok.soddisfatti)
    assert any("OS30" in s for s in ok.da_verificare)
    assert valuta_appalto(c, _appalto(regioni=["Lombardia"]), reg) is None
    assert valuta_appalto(c, _appalto(regioni=None), reg) is not None  # nazionale
    poca = valuta_appalto(
        c,
        _appalto(categorie_soa=[{"codice": "OG1", "tipo": "prevalente", "importo": "900000"}]),
        reg,
    )
    assert any("insufficiente" in s for s in poca.da_verificare)
    # categoria non posseduta e CPV non di interesse: niente
    software = _appalto(categorie_soa=None, cpv=["72000000"], divisioni_cpv=["72"])
    assert valuta_appalto(c, software, reg) is None
    # CPV solo secondaria: pertinente ma con riserva
    misto = _appalto(categorie_soa=None, cpv=["32000000", "45000000"], divisioni_cpv=["32", "45"])
    e = valuta_appalto(_cliente(soa_possedute=None), misto, reg)
    assert e and any("solo secondaria" in s for s in e.da_verificare)
    # profilo vuoto: nessun appalto
    vuoto = _cliente(soa_possedute=None, cpv_interesse=None)
    assert valuta_appalto(vuoto, _appalto(), regioni_cliente(vuoto)) is None
