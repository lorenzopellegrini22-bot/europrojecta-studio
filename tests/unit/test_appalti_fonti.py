"""Appalti: scheda e categorie SOA, Consip (righe reali), import ANAC, riassunto AI simulato."""

import csv
import io
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.appalti import consip
from app.appalti import scheda as sch
from app.appalti.scheda import estrai_cig, estrai_soa, prevalente, unisci_soa

FIXTURES = Path(__file__).parents[1] / "fixtures"
OGGI = date(2026, 11, 18)

# Dal testo di una scheda gara reale (SASI, Lanciano) fornita dal committente
DESCRIZIONE_SASI = (
    "FSC 2021-2027. Intervento «Razionalizzazione del sistema depurativo» CIG BD98FF7547\n"
    "Categoria Prevalente: OG6 Importo Euro 1.997.813,41\n"
    "Categoria Scorporabile: OS22 Importo Euro 848.493,38\n"
)


def test_estrazione_cig_e_soa():
    assert estrai_cig(DESCRIZIONE_SASI) == "BD98FF7547"
    assert estrai_cig("nessun codice qui", None) is None
    cats = unisci_soa(estrai_soa(DESCRIZIONE_SASI))
    assert [c["codice"] for c in cats] == ["OG6", "OS22"]
    og6, os22 = cats
    assert og6["tipo"] == "prevalente" and og6["importo"] == "1997813.41"
    assert os22["tipo"] == "scorporabile" and os22["importo"] == "848493.38"
    assert og6["descrizione"].startswith("Acquedotti, gasdotti")
    assert prevalente(cats)["codice"] == "OG6"
    # codici inesistenti ignorati; varianti con lettera riconosciute
    assert [c["codice"] for c in estrai_soa("OG99, OS 12-A e OS18B")] == ["OS12-A", "OS18-B"]


def test_unisci_soa_preferisce_anac():
    testo = [{"codice": "OG6", "tipo": None, "provenienza": "testo", "importo": "100"}]
    anac = [{"codice": "OG6", "tipo": "prevalente", "provenienza": "anac"}]
    (c,) = unisci_soa(testo, anac)
    assert c["tipo"] == "prevalente" and c["provenienza"] == "anac" and c["importo"] == "100"
    assert unisci_soa(None, []) is None


def test_testo_scheda_come_schede_gara():
    a = SimpleNamespace(
        categorie_soa=unisci_soa(estrai_soa(DESCRIZIONE_SASI)),
        stazione_appaltante="SASI SpA",
        ente_indirizzo="Zona Industriale n. 5 - 66034 Lanciano",
        ente_telefono="0872 724270",
        ente_email="sasispa@legalmail.it",
        ente_sito="http://www.sasispa.it",
        province=["Chieti"],
        regioni=["Abruzzo"],
        fonte="anac",
        cig="BD98FF7547",
        id_esterno="anac-BD98FF7547",
        titolo="Razionalizzazione del sistema depurativo",
        pubblicazione=date(2026, 11, 4),
        scadenza=date(2026, 12, 2),
        tipo_procedura="Procedura aperta",
        criterio_aggiudicazione="Offerta economicamente più vantaggiosa",
        luogo_esecuzione="Lanciano",
        citta=None,
        importo_stimato="2882628.25",
        descrizione=DESCRIZIONE_SASI,
        riferimenti_legge="art. 71 d.lgs. 36/2023",
        documenti=[{"titolo": "Bando di gara", "url": "https://esempio.it/bando.pdf"}],
        link_partecipazione=None,
        link=None,
    )
    t = sch.testo_scheda(a)
    assert "Categoria primaria\tOG6 - Acquedotti" in t and "(€ 1.997.813,41)" in t
    assert "Categorie secondarie\tOS22 - Impianti di potabilizzazione e depurazione" in t
    assert "Territorio\tCHIETI" in t and "Codice\tCIG BD98FF7547" in t
    assert "Termina\t02-12-2026" in t and "Importo\t€ 2.882.628,25" in t
    assert "Bando di gara: https://esempio.it/bando.pdf" in t


# --- Consip ------------------------------------------------------------------------------


def test_consip_righe_reali():
    righe = list(
        csv.DictReader(io.StringIO((FIXTURES / "consip_campione.csv").read_text(encoding="utf-8")))
    )
    corso, mepa, vecchia = (consip.interpreta(r, OGGI) for r in righe)
    assert corso["stazione_appaltante"] == "Consip S.p.A." and corso["cig"]
    assert corso["scadenza"] is None and "Gara in corso" in corso["descrizione"]
    assert corso["importo_stimato"] is not None
    assert (
        mepa["tipo_procedura"].startswith("MePA") and "Abilitazione aperta" in mepa["descrizione"]
    )
    assert vecchia is None  # già aggiudicata


# --- ANAC --------------------------------------------------------------------------------

# Colonne del file CIG come documentate (pnrr.datibenecomune.it, fonte ANAC)
INTESTAZIONE_CIG = (
    "cig;numero_gara;oggetto_gara;importo_complessivo_gara;n_lotti_componenti;oggetto_lotto;"
    "importo_lotto;oggetto_principale_contratto;stato;settore;luogo_istat;provincia;"
    "data_pubblicazione;data_scadenza_offerta;cod_tipo_scelta_contraente;tipo_scelta_contraente;"
    "denominazione_amministrazione_appaltante;sezione_regionale;cod_cpv;descrizione_cpv"
)


def _zip(nome: str, testo: str) -> io.BytesIO:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(nome, testo.encode("utf-8"))
    b.seek(0)
    return b


def test_anac_lettura_e_riconoscimento():
    from app.appalti import anac

    righe = list(
        anac._righe(
            _zip("cig.csv", INTESTAZIONE_CIG + "\nBD98FF7547;1;Depuratore àè;100;1;;100;LAVORI\n"),
            "cig_2026.zip",
        )
    )
    assert righe[0]["cig"] == "BD98FF7547" and righe[0]["oggetto_gara"] == "Depuratore àè"
    assert anac.tipo_file(list(righe[0])) == "cig"
    assert anac.tipo_file(["cig", "id_categoria", "cod_tipo_categoria"]) == "categorie"
    with pytest.raises(anac.ErroreImport):
        anac.tipo_file(["a", "b"])
    with pytest.raises(anac.ErroreImport):
        list(anac._righe(io.BytesIO(b"x"), "file.pdf"))
    assert anac._importo_anac("2882628.25") == pytest.approx(2882628.25)
    assert str(anac._importo_anac("2.882.628,25")) == "2882628.25"
    assert anac._data("2026-12-02") == date(2026, 12, 2)
    assert anac._data("02/12/2026") == date(2026, 12, 2)
    assert anac._tipo_categoria("PREVALENTE") == "prevalente"
    assert anac._tipo_categoria("S") == "scorporabile"


# --- Riassunto AI (client finto) ---------------------------------------------------------


def test_riassunto_usa_modello_delle_impostazioni(monkeypatch):
    from app.appalti import riassunto

    chiamate = []

    class Risposte:
        def create(self, **kw):
            chiamate.append(kw)
            return SimpleNamespace(output_text="1. In breve\nGara per un depuratore.")

    client = SimpleNamespace(responses=Risposte())
    monkeypatch.setattr(riassunto, "disponibile", lambda db: (True, ""))
    monkeypatch.setattr(
        riassunto.integrazioni,
        "leggi_valori",
        lambda db, codice: {"api_key": "k", "modello": "modello-scelto"},
    )
    db = SimpleNamespace(commit=lambda: None)
    a = SimpleNamespace(
        **{k: None for k in ("categorie_soa", "ente_indirizzo", "ente_telefono", "ente_email")},
        **{k: None for k in ("ente_sito", "province", "regioni", "cig", "pubblicazione")},
        **{k: None for k in ("scadenza", "tipo_procedura", "criterio_aggiudicazione")},
        **{k: None for k in ("luogo_esecuzione", "citta", "importo_stimato", "descrizione")},
        **{k: None for k in ("riferimenti_legge", "documenti", "link_partecipazione", "link")},
        stazione_appaltante="SASI",
        fonte="ted",
        id_esterno="1-2026",
        titolo="Depuratore",
    )
    testo = riassunto.genera(db, a, client=client)
    assert testo.startswith("1. In breve") and a.riassunto_modello == "modello-scelto"
    assert chiamate[0]["model"] == "modello-scelto"
    assert "Depuratore" in chiamate[0]["input"] and "non inventare" in chiamate[0]["instructions"]
