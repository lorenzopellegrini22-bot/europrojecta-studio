"""Clienti enti pubblici: comuni della provincia di Viterbo (prova del 23/11/2026)."""

from types import SimpleNamespace as N

from app.opportunita.abbinamento import PUNTEGGIO_MINIMO, valuta_bando, valuta_bando_con_motivo
from app.opportunita.valutazione import profilo_anonimo
from app.utils.enti import limite_abitanti, tipo_dal_nome


def _comune(nome="Bassano in Teverina", abitanti=1300):
    sede = N(tipo="legale", comune_testo=nome, provincia="VT", regione="Lazio", codice_istat=None)
    return N(
        ragione_sociale=f"Comune di {nome}", tipo_soggetto="ente_pubblico", abitanti=abitanti,
        forma_giuridica="Comune", sedi=[sede], sede_legale=sede, ateco_primario=None,
        dimensione_manuale=None, data_costituzione=None, plafond_de_minimis_manuale=None,
        soa_possedute=None, regioni_interesse=None, cpv_interesse=None,
    )  # fmt: skip


def _bando(titolo, tipologie=("Ente Pubblico",), regioni=("Lazio",), descrizione="", **req):
    r = {
        "tipologie_soggetto": list(tipologie) if tipologie else None,
        "territorio": "regionale" if regioni else "nazionale",
        "regioni": list(regioni) if regioni else None,
        "comuni": None,
        "dimensioni": ["micro", "piccola", "media"],
        "ateco_ammessi": ["10", "25"],
        "regime": "de_minimis",
    }
    r.update(req)
    return N(titolo=titolo, stato="aperto", descrizione=descrizione, ente=None,
             requisiti=N(**r), raw={})  # fmt: skip


def test_tipo_dal_nome():
    assert tipo_dal_nome("Comune di Bassano in Teverina") == "ente_pubblico"
    assert tipo_dal_nome("COMUNE DI CIVITA CASTELLANA") == "ente_pubblico"
    assert tipo_dal_nome("Unione dei Comuni della Tuscia") == "ente_pubblico"
    assert tipo_dal_nome("Provincia di Viterbo") == "ente_pubblico"
    assert tipo_dal_nome("Ente X", "Ente pubblico") == "ente_pubblico"
    assert tipo_dal_nome("Comunicazione Tuscia S.r.l.") == "impresa"
    assert tipo_dal_nome("Officine Tuscia S.r.l.", "S.r.l.") == "impresa"


def test_bando_per_i_comuni_del_lazio_per_ogni_comune():
    """Parcheggi urbani nei Comuni del Lazio: i 5.000 abitanti cambiano solo la percentuale
    del contributo, non escludono Viterbo (67.000)."""
    testo = "100% del quadro economico per i comuni con popolazione fino a 5.000 abitanti"
    b = _bando("Contributi per la realizzazione di parcheggi urbani nei Comuni del Lazio",
               descrizione=testo)  # fmt: skip
    for nome, abitanti in (("Bassano in Teverina", 1300), ("Viterbo", 67000)):
        es = valuta_bando(_comune(nome, abitanti), b)
        assert es is not None and es.punteggio >= 80, (nome, es)
        assert "Bando riservato agli enti pubblici" in es.soddisfatti
    # ATECO, dimensione e de minimis non contano per un ente
    assert not any("ATECO" in x or "minimis" in x for x in es.da_verificare + es.soddisfatti)


def test_esclusioni_per_un_comune():
    c = _comune()
    per_imprese = _bando("Voucher digitali per le PMI", tipologie=("Impresa",))
    esito, motivo = valuta_bando_con_motivo(c, per_imprese)
    assert esito is None and motivo.startswith("Bando non rivolto agli enti pubblici")
    assert valuta_bando(c, _bando("Comuni toscani", regioni=("Toscana",))) is None
    # «piccoli comuni» nel titolo: requisito certo
    piccoli = _bando("Fondo per i piccoli comuni del Lazio")
    assert valuta_bando(c, piccoli) is not None
    assert valuta_bando(_comune("Viterbo", 67000), piccoli) is None
    senza = valuta_bando(_comune(abitanti=None), piccoli)
    assert any("indica gli abitanti" in x for x in senza.da_verificare)


def test_beneficiari_non_indicati_e_misti():
    c = _comune()
    dal_titolo = valuta_bando(c, _bando("Contributi agli enti locali per le scuole", tipologie=()))
    assert dal_titolo.punteggio >= PUNTEGGIO_MINIMO
    # viaggi di istruzione della Regione Lazio senza beneficiari: sotto la soglia
    viaggi = valuta_bando(c, _bando("Viaggi di istruzione degli studenti", tipologie=()))
    assert viaggi.punteggio < PUNTEGGIO_MINIMO
    # enti tra i beneficiari ma il titolo parla di imprese: sotto la soglia
    misto = _bando("Automotive - Accordi per l'innovazione nella filiera", regioni=None,
                   tipologie=("Impresa", "Ente Pubblico", "Rete", "Università"))  # fmt: skip
    assert valuta_bando(c, misto).punteggio < PUNTEGGIO_MINIMO
    sport = _bando("Fondo di garanzia per l'impiantistica sportiva", regioni=None,
                   tipologie=("Ente Pubblico", "Impresa"))  # fmt: skip
    assert valuta_bando(c, sport).punteggio >= PUNTEGGIO_MINIMO


def test_altri_enti_e_bandi_dello_stesso_comune():
    """Prova del 23/11/2026 su Sondrio e Pedavena."""
    sondrio = _comune("Sondrio", 21000)
    sondrio.sedi[0].provincia, sondrio.sedi[0].regione = "SO", "Lombardia"
    aler = _bando("Sicurezza Aler lombarde 2025", regioni=("Lombardia",))
    es = valuta_bando(sondrio, aler)
    assert es.punteggio < PUNTEGGIO_MINIMO and any("ALER" in x for x in es.da_verificare)
    dote = _bando("Provincia di Sondrio - Dote Impresa Collocamento Mirato",
                  regioni=("Lombardia",), tipologie=("Ente Pubblico", "Impresa"))  # fmt: skip
    assert valuta_bando(sondrio, dote).punteggio < PUNTEGGIO_MINIMO
    pedavena = _comune("Pedavena", 4300)
    proprio = _bando("Bando riqualificazione caseificio - Comune di Pedavena")
    assert valuta_bando(pedavena, proprio).punteggio < PUNTEGGIO_MINIMO
    # lo stesso bando per un altro comune non cambia per questa regola
    assert valuta_bando(_comune(), proprio).punteggio >= PUNTEGGIO_MINIMO


def test_professionisti_organismi_e_bandi_per_tutti():
    """Prova su 12 enti in 9 regioni (23/11/2026)."""
    c = _comune()
    # la fonte indica solo «Ente Pubblico», ma il titolo è per i professionisti
    prof = _bando("FVG - Aggiornamento professionale da parte dei professionisti")
    assert valuta_bando(c, prof).punteggio < PUNTEGGIO_MINIMO
    org = _bando("Contributi in favore di organismi regionali di certificazione",
                 tipologie=("Professionista", "Ente Pubblico", "Consorzio"))  # fmt: skip
    assert valuta_bando(c, org).punteggio < PUNTEGGIO_MINIMO
    tutti = ("Ente Pubblico", "Impresa", "Cittadino", "Consorzio", "Professionista",
             "Rete", "Istituto finanziario", "Università", "Cooperative")  # fmt: skip
    largo = valuta_bando(c, _bando("Percorsi di formazione obbligatoria", tipologie=tutti))
    stretto = valuta_bando(c, _bando("Percorsi di formazione obbligatoria"))
    assert largo.punteggio <= stretto.punteggio - 30


def test_imprese_come_prima():
    impresa = _comune()
    impresa.tipo_soggetto = "impresa"
    impresa.ateco_primario = N(codice="25.62", versione="2007")
    impresa.dimensione_manuale = "piccola"
    assert valuta_bando(impresa, _bando("Parcheggi nei Comuni del Lazio")) is None


def test_limite_abitanti_e_profilo_anonimo():
    assert limite_abitanti("Comuni con popolazione fino a 5.000 abitanti") == 5000
    assert limite_abitanti("comuni non superiori a 15.000 abitanti") == 15000
    assert limite_abitanti("Bando per i Piccoli Comuni") == 5000
    assert limite_abitanti("Contributi alle imprese") is None
    c = _comune()
    c.sedi[0].tipo = "legale"
    testo = profilo_anonimo(c)
    assert "Ente pubblico" in testo and "fino a 3.000 abitanti" in testo
    assert "Bassano" not in testo and "1300" not in testo and "1.300" not in testo


def test_report_per_un_ente():
    from app.report.servizio import sezioni

    titoli = dict(sezioni(_comune()))
    assert titoli["perche_adatto"] == "Perché è adatto al vostro ente"
    assert dict(sezioni(N(tipo_soggetto="impresa")))["perche_adatto"].endswith("vostra impresa")
