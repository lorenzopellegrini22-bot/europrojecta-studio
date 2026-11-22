"""Gare dalla Piattaforma di Pubblicità a Valore Legale di ANAC (anche sotto soglia).

Gli avvisi sono costruiti qui con la stessa struttura delle risposte della piattaforma
(verifica del 22/11/2026), con dati di prova."""

import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.appalti import pvl
from app.models.appalti import Appalto

pytestmark = pytest.mark.integration

OGGI = date(2026, 11, 22)


def _avviso(cpv="45233120_Lavori di costruzione di strade", ted=None, scadenza="2026-12-10",
            id_appalto=None, id_avviso=None, tipo="avviso", categorie=None, luogo_nuts="Padova",
            luogo_istat="PADOVA", cig=None,
            pubblicato="2026-11-20T07:00:00.000+00:00"):  # fmt: skip
    return {
        "idAvviso": id_avviso or str(uuid.uuid4()),
        "idAppalto": id_appalto or str(uuid.uuid4()),
        "codiceScheda": "P1_16" if ted else "P2_16",
        "dataScadenza": f"{scadenza}T11:00:00.000+00:00",
        "dataPubblicazione": pubblicato,
        "tipo": tipo,
        "attivo": True,
        "oscurato": False,
        "templates": [
            {
                "lingua": "it",
                "template": {
                    "metadata": {
                        "link_eform_ted": ted
                        and f"https://ted.europa.eu/udl?uri=TED:NOTICE:{ted}:TEXT:IT:HTML",
                        "titolo": None,
                        "descrizione": "Lavori di sistemazione delle aree l&#8217;esterno",
                    },
                    "sections": [
                        {
                            "name": "SEZ. A - Committente",
                            "fields": {
                                "soggetti_sa": [
                                    {"denominazione_amministrazione": "COMUNE DI PROVA"}
                                ]
                            },
                        },  # fmt: skip
                        {
                            "name": "SEZ. B - Dati Generali",
                            "fields": {
                                "documenti_di_gara_link": "https://comune.example/gara/1",
                                "tipo_procedura_aggiudicazione": "Aperta",
                            },
                        },  # fmt: skip
                        {
                            "name": "SEZ. C - Oggetto",
                            "items": [
                                {
                                    "cig": cig or uuid.uuid4().hex[:10].upper(),
                                    "natura_principale": "Lavori",
                                    "descrizione": "Lavori di sistemazione",
                                    "cpv": cpv,
                                    "valore_complessivo_stimato": 292800.0,
                                    "luogo_nuts": luogo_nuts,
                                    "luogo_istat": luogo_istat,
                                    "categorie": categorie
                                    if categorie is not None
                                    else [{"codice": "OG 1"}, {"codice": "OS 18-B"}],
                                }
                            ],
                        },
                    ],
                },
            }
        ],
    }


def test_interpretazione_degli_avvisi():
    d = pvl.interpreta(_avviso(), OGGI)
    assert d["titolo"] == "Lavori di sistemazione delle aree l’esterno"  # entità HTML
    assert d["cpv"] == ["45233120"] and d["divisioni_cpv"] == ["45"]
    assert d["codici_soa"] == ["OG1", "OS18-B"] and d["natura"] == ["lavori"]
    assert (d["province"], d["regioni"]) == (["Padova"], ["Veneto"])
    assert d["scadenza"] == date(2026, 12, 10) and d["citta"] == "Padova"
    assert d["documenti"][0]["url"] == "https://comune.example/gara/1"
    assert d["link"].startswith("https://pubblicitalegale.anticorruzione.it/bandi/")
    # scaduta, oscurata: non servono
    assert pvl.interpreta(_avviso(scadenza="2026-11-01"), OGGI) is None
    assert pvl.interpreta({**_avviso(), "oscurato": True}, OGGI) is None
    # l'elenco ha solo la descrizione del CPV: niente codice inventato
    assert pvl.interpreta(_avviso(cpv="Servizi di mensa"), OGGI)["cpv"] is None
    # piccole cose
    assert pvl.codice_soa("OG 1") == "OG1" and pvl.codice_soa("OS 2-A") == "OS2-A"
    assert pvl.codice_soa("FS") is None
    assert pvl.numero_ted("https://ted.europa.eu/udl?uri=TED:NOTICE:00810170-2026:TEXT") == (
        "810170-2026"
    )
    assert pvl.nome_comune("FORNOVO DI TARO") == "Fornovo di Taro"
    assert pvl.nome_comune("SANTA MARIA A MONTE") == "Santa Maria a Monte"
    assert pvl.provincia_e_regione("Reggio Emilia") == ("Reggio nell’Emilia", "Emilia-Romagna")
    assert pvl.provincia_e_regione("Lazio") == (None, "Lazio")


def test_raccolta_sotto_soglia_rettifiche_e_ted(db, monkeypatch):
    from app.sources import http

    monkeypatch.setattr(http, "INTERVALLO_MINIMO_SECONDI", 0)
    db.execute(delete(Appalto).where(Appalto.id_esterno.like("pvl-%")))
    numero = f"{uuid.uuid4().int % 900000 + 100000}-2026"
    gara_ted = Appalto(fonte="ted", id_esterno=numero, titolo="Gara europea", raw={},
                       scadenza=date(2026, 12, 30))  # fmt: skip
    db.add(gara_ted)
    db.commit()
    id_appalto = str(uuid.uuid4())
    originale = _avviso(id_appalto=id_appalto, pubblicato="2026-11-19T07:00:00.000+00:00")
    rettifica = _avviso(id_appalto=id_appalto, tipo="rettifica", scadenza="2027-01-15")
    sopra_soglia = _avviso(ted=numero, cig="BD72382000")
    sopra_soglia_non_su_ted = _avviso(ted="999999-2026")
    elenco = [originale, rettifica, sopra_soglia, sopra_soglia_non_su_ted]
    richieste: list[str] = []

    def gestore(req: httpx.Request) -> httpx.Response:
        richieste.append(str(req.url))
        if req.url.path == "/api/v0/avvisi":
            giorno = req.url.params["dataPubblicazioneStart"]
            contenuto = elenco if giorno == "20/11/2026" else []
            return httpx.Response(200, json={"content": contenuto, "last": True})
        id_avviso = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=next(a for a in elenco if a["idAvviso"] == id_avviso))

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        esito = pvl.raccogli(db, c, oggi=OGGI)
    assert "1 gare nuove" in esito and "1 gare TED completate" in esito
    gare = list(db.scalars(select(Appalto).where(Appalto.id_esterno.like("pvl-%"))))
    assert len(gare) == 1  # la rettifica prevale sull'originale, niente doppioni TED
    assert gare[0].fonte == "anac" and gare[0].scadenza == date(2027, 1, 15)
    db.refresh(gara_ted)
    assert gara_ted.cig == "BD72382000" and gara_ted.codici_soa == ["OG1", "OS18-B"]
    # la notte dopo lo stesso avviso non si richiede di nuovo
    dettagli_prima = sum("/avvisi/" in r for r in richieste)
    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        esito = pvl.raccogli(db, c, oggi=OGGI)
    assert "0 gare nuove" in esito
    assert sum("/avvisi/" in r for r in richieste) == dettagli_prima
    assert sum("dataPubblicazioneStart" in r for r in richieste) == pvl.GIORNI_ELENCO * 2
    db.execute(delete(Appalto).where(Appalto.id.in_([gara_ted.id, gare[0].id])))
    db.commit()


def test_gare_oltre_il_limite_arrivano_la_notte_dopo(db, monkeypatch):
    """Ogni notte si rilegge l'elenco di 60 giorni: le gare rimaste fuori dal limite dei
    dettagli (anche pubblicate settimane prima) arrivano le notti successive."""
    from app.sources import http

    monkeypatch.setattr(http, "INTERVALLO_MINIMO_SECONDI", 0)
    monkeypatch.setattr(pvl, "MAX_DETTAGLI", 2)
    db.execute(delete(Appalto).where(Appalto.id_esterno.like("pvl-%")))
    db.commit()
    vecchie = [_avviso(pubblicato="2026-10-01T07:00:00.000+00:00") for _ in range(3)]

    def gestore(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v0/avvisi":
            contenuto = vecchie if req.url.params["dataPubblicazioneStart"] == "01/10/2026" else []
            return httpx.Response(200, json={"content": contenuto, "last": True})
        id_avviso = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=next(a for a in vecchie if a["idAvviso"] == id_avviso))

    with httpx.Client(transport=httpx.MockTransport(gestore)) as c:
        prima = pvl.raccogli(db, c, oggi=OGGI)
        seconda = pvl.raccogli(db, c, oggi=OGGI)
    assert "2 gare nuove" in prima and "1 rimandate alla prossima notte" in prima
    assert "1 gare nuove" in seconda and "rimandate" not in seconda
    assert db.scalar(select(func.count()).where(Appalto.id_esterno.like("pvl-%"))) == 3
    db.execute(delete(Appalto).where(Appalto.id_esterno.like("pvl-%")))
    db.commit()
