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


def test_nuovi_filtri_con_motivo_di_esclusione():
    from app.opportunita.abbinamento import Contesto, famiglia_forma, valuta_bando_con_motivo

    oggi = date(2026, 11, 18)
    c = _cliente(
        dimensione_manuale=None,
        dipendenti=8,
        fatturato=Decimal("900000"),
        totale_attivo=None,
        forma_giuridica="S.r.l.",
        data_costituzione=date(2025, 6, 1),
        plafond_de_minimis_manuale=None,
    )
    # dimensione stimata: non esclude mai, al massimo «da verificare»
    es, motivo = valuta_bando_con_motivo(c, _bando(dimensioni=["media"]), Contesto(oggi=oggi))
    assert es is not None and motivo is None
    assert any("stimata: micro" in m for m in es.da_verificare)
    # anzianità: costituita da 17 mesi
    _, motivo = valuta_bando_con_motivo(c, _bando(anzianita_max_mesi=12), Contesto(oggi=oggi))
    assert motivo and "al massimo 12" in motivo
    es, _ = valuta_bando_con_motivo(c, _bando(anzianita_max_mesi=36), Contesto(oggi=oggi))
    assert any("Anzianità compatibile" in m for m in es.soddisfatti)
    senza_data = _cliente(data_costituzione=None)
    es, _ = valuta_bando_con_motivo(senza_data, _bando(anzianita_max_mesi=12), Contesto(oggi=oggi))
    assert any("manca la data" in m for m in es.da_verificare)
    # forma giuridica: esclude solo se entrambe riconosciute
    assert famiglia_forma("Società a responsabilità limitata") == "società di capitali"
    _, motivo = valuta_bando_con_motivo(
        c, _bando(forme_giuridiche=["Società cooperative"]), Contesto(oggi=oggi)
    )
    assert motivo and "Forma giuridica" in motivo
    es, _ = valuta_bando_con_motivo(
        c, _bando(forme_giuridiche=["Soggetti iscritti all'albo X"]), Contesto(oggi=oggi)
    )
    assert es is not None
    # de minimis: plafond noto e insufficiente → escluso; non noto → da verificare
    aiuti = [
        N(data_concessione=date(2025, 1, 1), importo=Decimal("295000"), regolamento="generale")
    ]
    _, motivo = valuta_bando_con_motivo(
        c, _bando(regime="de_minimis", importo_min=Decimal("10000")), Contesto(aiuti, oggi=oggi)
    )
    assert motivo and "Plafond" in motivo
    es, _ = valuta_bando_con_motivo(
        c, _bando(regime="de_minimis", importo_min=Decimal("10000")), Contesto(oggi=oggi)
    )
    assert any("plafond del cliente non noto" in m for m in es.da_verificare)
    # progetti: spese in comune → progetto più adatto
    progetto = N(id=7, titolo="Linea", attivo=True, categorie=["digitale e software"])
    es, _ = valuta_bando_con_motivo(
        c,
        _bando(categorie_spesa=["digitale e software", "formazione"]),
        Contesto(progetti=[progetto], oggi=oggi),
    )
    assert es.progetto_id == 7 and any("Linea" in m for m in es.soddisfatti)


def test_settore_solo_a_inizio_parola_e_enti_locali():
    from app.opportunita.abbinamento import _sezione_settore, ente_locale

    # «esportatrici» contiene «sport»: prima escludeva il bando per tutti (trovato dal collaudo)
    assert _sezione_settore("Indennizzo danni alle imprese esportatrici") is None
    assert _sezione_settore("Contributi alle associazioni sportive")[1] == "sport"
    assert ente_locale(N(ente="COMUNE DI TREVIGLIO", titolo="Bando MPMI")) == "COMUNE DI TREVIGLIO"
    assert ente_locale(N(ente="Ministero delle Imprese", titolo="Bando nazionale")) is None
    c = _cliente()
    locale = _bando(territorio="sconosciuto", regioni=None)
    locale.ente, locale.titolo = "Comune di Treviglio", "Contributi alle MPMI"
    esito = valuta_bando(c, locale)
    assert esito is not None and esito.punteggio < 45  # non escluso, ma sotto soglia


def test_regole_trovate_simulando_il_lavoro_dello_studio():
    """Casi trovati usando la piattaforma come il consulente (18/11/2026)."""
    from app.opportunita.abbinamento import Contesto, valuta_bando_con_motivo

    oggi = date(2026, 11, 18)
    officina = _cliente(
        ateco_primario=N(codice="25.62", versione="2007"),
        forma_giuridica="S.r.l.",
        dimensione_manuale=None,
        dipendenti=18,
        fatturato=Decimal("2400000"),
        totale_attivo=None,
        data_costituzione=date(2009, 4, 15),
        plafond_de_minimis_manuale=None,
    )

    def bando(titolo="Contributi alle imprese", descrizione=None, raw=None, ente=None, **req):
        b = _bando(titolo=titolo, **req)
        b.descrizione, b.raw, b.ente = descrizione, raw or {}, ente
        b.data_apertura, b.data_chiusura = req.pop("apertura", None), None
        return b

    ctx = Contesto(oggi=oggi)
    # settori della fonte nazionale
    _, motivo = valuta_bando_con_motivo(
        officina, bando(raw={"Settore_Attivita": ["Turismo", "Cultura", "Commercio"]}), ctx
    )
    assert motivo and "Settore" in motivo
    es, _ = valuta_bando_con_motivo(
        officina, bando(raw={"Settore_Attivita": ["Meccanica", "Turismo"]}), ctx
    )
    assert any("Settore ammesso" in m for m in es.soddisfatti)
    # bando solo per professionisti: escluso per una S.r.l.
    _, motivo = valuta_bando_con_motivo(officina, bando(tipologie_soggetto=["Professionista"]), ctx)
    assert motivo and "professionisti" in motivo
    # bando senza beneficiari ma chiaramente non per imprese: sotto soglia
    es, _ = valuta_bando_con_motivo(
        officina,
        bando(titolo="PRESEPI 2026 per le parrocchie e i cittadini", tipologie_soggetto=None),
        ctx,
    )
    assert es is not None and es.punteggio < 45
    # testo per imprese in avvio e impresa di 17 anni: sotto soglia
    es, _ = valuta_bando_con_motivo(
        officina,
        bando(descrizione="Prestiti a microimprese in fase di avviamento, anche non costituite."),
        ctx,
    )
    assert es is not None and es.punteggio < 45 and any("17 anni" in m for m in es.da_verificare)


def test_uno_o_due_settori_non_escludono():
    """Conto Termico 3.0: la fonte indica solo «Edilizia» (tipo di intervento)."""
    from app.opportunita.abbinamento import Contesto, valuta_bando_con_motivo

    hotel = _cliente(ateco_primario=N(codice="55.10", versione="2007"))
    b = _bando(titolo="Conto Termico 3.0", territorio="nazionale", regioni=None)
    b.raw, b.descrizione = {"Settore_Attivita": ["Edilizia"]}, None
    es, motivo = valuta_bando_con_motivo(hotel, b, Contesto())
    assert motivo is None and any("tipo di intervento" in m for m in es.da_verificare)


def test_esigenze_del_cliente_contano_nel_punteggio():
    """Prova del 21/11/2026: un'officina che vuole comprare macchinari riceveva la Nuova
    Sabatini al 76° posto, dietro a bandi di formazione con più dati compilati."""
    from app.opportunita.abbinamento import Contesto, valuta_bando_con_motivo

    oggi = date(2026, 11, 21)
    officina = _cliente(data_costituzione=date(2005, 1, 1))
    macchinari = N(id=1, titolo="Centro di lavoro CNC", descrizione=None, attivo=True,
                   categorie=["macchinari e attrezzature"])  # fmt: skip
    ctx = Contesto(progetti=[macchinari], oggi=oggi)

    def bando(titolo, descrizione=None, finalita=None):
        b = _bando(titolo=titolo, territorio="nazionale", regioni=None, finalita=finalita)
        b.descrizione, b.raw, b.ente = descrizione, {}, None
        b.data_apertura = b.data_chiusura = None
        return b

    sabatini, _ = valuta_bando_con_motivo(
        officina, bando("Beni strumentali - Nuova Sabatini", "Acquisto di nuovi macchinari"), ctx
    )
    formazione, _ = valuta_bando_con_motivo(
        officina, bando("Formazione continua", finalita=["Formazione (lavoro, occupazione)"]), ctx
    )
    solo_finalita, _ = valuta_bando_con_motivo(
        officina, bando("Misura regionale 3.1", finalita=["Sostegno investimenti"]), ctx
    )
    assert sabatini.progetto_id == 1 and any("CNC" in m for m in sabatini.soddisfatti)
    assert any("progetti del cliente" in m for m in formazione.da_verificare)
    assert any("solo la finalità" in m for m in solo_finalita.da_verificare)
    assert sabatini.punteggio > solo_finalita.punteggio > formazione.punteggio
    # senza progetti non cambia nulla
    senza, _ = valuta_bando_con_motivo(officina, bando("Formazione continua"), Contesto(oggi=oggi))
    assert senza.punteggio > formazione.punteggio
    # progetto senza categorie: si ricavano dal testo
    da_testo = N(id=2, titolo="Fotovoltaico sul capannone", descrizione=None, attivo=True,
                 categorie=None)  # fmt: skip
    es, _ = valuta_bando_con_motivo(
        officina, bando("Conto Termico 3.0"), Contesto(progetti=[da_testo], oggi=oggi)
    )
    assert es.progetto_id == 2


def test_impresa_giovane_e_camera_di_commercio_con_nome_lungo():
    from app.opportunita.abbinamento import Contesto, valuta_bando_con_motivo
    from app.riferimenti.territorio import Territorio

    oggi = date(2026, 11, 21)
    b = _bando(titolo="Smart&Start Italia", territorio="nazionale", regioni=None)
    b.descrizione, b.raw, b.ente = "Per l'avvio e lo sviluppo delle startup innovative", {}, None
    b.data_apertura = b.data_chiusura = None
    giovane, _ = valuta_bando_con_motivo(
        _cliente(data_costituzione=date(2025, 2, 1)), b, Contesto(oggi=oggi)
    )
    vecchia, _ = valuta_bando_con_motivo(
        _cliente(data_costituzione=date(2005, 2, 1)), b, Contesto(oggi=oggi)
    )
    assert any("imprese nuove" in m for m in giovane.soddisfatti)
    assert giovane.punteggio > vecchia.punteggio + 40
    # «…Agricoltura di Brescia» tagliato a 60 caratteri perdeva la provincia
    t = Territorio(sigla_da_provincia={"brescia": "BS", "bergamo": "BG"})
    bergamo = _cliente(sede_legale=N(regione="Lombardia", comune_testo="Bergamo", provincia="BG"))
    cciaa = _bando(titolo="Voucher digitali", regioni=["Lombardia"])
    cciaa.ente = "Camera di Commercio, Industria, Artigianato e Agricoltura di Brescia"
    cciaa.descrizione, cciaa.raw, cciaa.data_apertura, cciaa.data_chiusura = None, {}, None, None
    es, _ = valuta_bando_con_motivo(bergamo, cciaa, Contesto(oggi=oggi, territorio=t))
    assert any("BS" in m for m in es.da_verificare) and es.punteggio < 45


def test_collaudo_su_50_aziende():
    """Errori trovati il 21/11/2026 simulando 50 aziende sui bandi veri."""
    from app.opportunita.abbinamento import (
        Contesto,
        Esito,
        finanzia,
        punteggio_finale,
        senza_titoli_ripetuti,
        valuta_bando_con_motivo,
    )
    from app.riferimenti.territorio import Territorio, VoceComune

    oggi = date(2026, 11, 21)
    t = Territorio(sigla_da_provincia={"bari": "BA", "lecce": "LE", "bolzano": "BZ",
                                       "trento": "TN", "udine": "UD", "padova": "PD"})  # fmt: skip
    t.per_nome["cividale del friuli"].append(
        VoceComune("030026", "Cividale del Friuli", "UD", "Udine", "Friuli-Venezia Giulia")
    )

    def bando(titolo, ente=None, **req):
        b = _bando(titolo=titolo, **req)
        b.ente, b.descrizione, b.raw = ente, None, {}
        b.data_apertura = b.data_chiusura = None
        return b

    # Camera di commercio di un'altra provincia: sotto la soglia anche con tutti i bonus
    hotel = _cliente(sede_legale=N(regione="Puglia", comune_testo="Lecce", provincia="LE"),
                     ateco_primario=N(codice="55.10", versione="2007"))  # fmt: skip
    camera = "Camera di Commercio, Industria, Artigianato e Agricoltura di Bari"
    cciaa = bando("Voucher turismo 2026", regioni=["Puglia"], ateco_ammessi=["55"], ente=camera)
    es, _ = valuta_bando_con_motivo(hotel, cciaa, Contesto(oggi=oggi, territorio=t))
    assert es.punteggio < 45
    # Provincia autonoma di Bolzano per un'impresa di Trento
    trento = _cliente(sede_legale=N(regione="Trentino-Alto Adige/Südtirol", comune_testo="Trento",
                                    provincia="TN"))  # fmt: skip
    bz = bando("Contributi alle imprese", regioni=["Trentino-Alto Adige/Südtirol"],
               ente="Provincia Autonoma di Bolzano - Ripartizione Economia")  # fmt: skip
    es, _ = valuta_bando_con_motivo(trento, bz, Contesto(oggi=oggi, territorio=t))
    assert es.punteggio < 45 and any("BZ" in m for m in es.da_verificare)
    # Comune senza territorio indicato: la provincia si ricava dal nome del comune
    padova = _cliente(sede_legale=N(regione="Veneto", comune_testo="Padova", provincia="PD"))
    cividale = bando("Contributi alle imprese", territorio="sconosciuto", regioni=None,
                     ente="COMUNE DI CIVIDALE DEL FRIULI")  # fmt: skip
    es, _ = valuta_bando_con_motivo(padova, cividale, Contesto(oggi=oggi, territorio=t))
    assert es.punteggio < 45 and any("UD" in m for m in es.da_verificare)
    # «impianti» da solo (fotovoltaici, sportivi) non vuol dire macchinari
    ferx = bando("FERX - Accesso diretto per impianti fino a 1 MW")
    assert "macchinari e attrezzature" not in finanzia(ferx, ferx.requisiti)["titolo"]
    # ATECO ammesso da un bando aperto a quasi tutti i settori: pochi punti
    tutti = [f"{d:02d}" for d in range(1, 100)]
    ampio, _ = valuta_bando_con_motivo(_cliente(), bando("A", ateco_ammessi=tutti), Contesto())
    stretto, _ = valuta_bando_con_motivo(_cliente(), bando("B", ateco_ammessi=["41"]), Contesto())
    assert stretto.punteggio - ampio.punteggio == 15
    # punteggio: sopra 80 si comprime ma l'ordine resta
    assert punteggio_finale(44) == 44 and punteggio_finale(80) == 80
    assert punteggio_finale(105) < punteggio_finale(125) < punteggio_finale(150) < 100
    # stessa misura in due schede della stessa fonte: una sola opportunità
    gruppo = [("bando", 1, Esito(90)), ("bando", 2, Esito(80)), ("bando", 3, Esito(70))]
    titoli = {1: "Servizi di vicinato - Bolzano", 2: "Servizi di vicinato – Bolzano", 3: "Altro"}
    assert [g[1] for g in senza_titoli_ripetuti(gruppo, titoli)] == [1, 3]


def test_codici_cpv_piu_precisi_delle_divisioni():
    """Collaudo del 22/11/2026: con la sola divisione 79 un istituto di vigilanza riceveva
    gare di grafica ed eventi; con la 90 un'impresa di pulizie riceveva gare sui rifiuti."""
    from app.utils.cpv import codici_precisi, corrisponde, etichetta, interessi_effettivi

    assert codici_precisi("79710000, 909;  90000000 12345 abc") == ["7971", "909", "90"]
    assert codici_precisi("30000000 80100000") == ["30", "801"]  # divisioni che finiscono per 0
    assert interessi_effettivi(["79", "7971", "90"]) == {"7971", "90"}
    assert corrisponde("79713000", {"79", "7971"}) == "7971"
    assert corrisponde("79342000", {"7971"}) is None
    assert etichetta("7971").endswith("(CPV 7971…)")

    vigilanza = _cliente(soa_possedute=None, cpv_interesse=["79", "7971"],
                         sede_legale=N(regione="Lazio", comune_testo="Roma"))  # fmt: skip
    reg = regioni_cliente(vigilanza)
    guardia = _appalto(categorie_soa=None, natura=["servizi"], cpv=["79713000"],
                       divisioni_cpv=["79"])  # fmt: skip
    grafica = _appalto(categorie_soa=None, natura=["servizi"], cpv=["79822500"],
                       divisioni_cpv=["79"])  # fmt: skip
    es = valuta_appalto(vigilanza, guardia, reg)
    assert es and any("CPV 7971" in m for m in es.soddisfatti)
    assert valuta_appalto(vigilanza, grafica, reg) is None
    # solo la divisione: come prima
    generico = _cliente(soa_possedute=None, cpv_interesse=["79"])
    assert valuta_appalto(generico, grafica, regioni_cliente(generico)) is not None
