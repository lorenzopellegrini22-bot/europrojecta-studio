from app.importer import csv_clienti as imp


def test_codifica_separatore_e_mappatura():
    testo = "Ragione Sociale;P.IVA;Città;Prov;Fatturato;Data costituzione;Nome referente\n"
    dati = (testo + "Rossi Srl;IT01234567897;Viterbo;vt;1.200.000;15/03/2019;Anna\n").encode(
        "cp1252"
    )
    contenuto, codifica = imp.decodifica(dati)
    assert codifica == "cp1252" and "Città" in contenuto
    sep = imp.separatore(contenuto)
    assert sep == ";"
    intestazioni, righe = imp.leggi(contenuto, sep)
    mappa = imp.indovina_mappatura(intestazioni)
    assert mappa["Ragione Sociale"] == "ragione_sociale"
    assert mappa["P.IVA"] == "partita_iva"
    assert mappa["Città"] == "comune" and mappa["Prov"] == "provincia"
    assert mappa["Nome referente"] == "referente_nome"
    r = imp.valida(2, righe[0], intestazioni, mappa)
    imp.valida_azienda(r)
    assert not r.errori
    assert r.valori["partita_iva"] == "01234567897" and r.valori["provincia"] == "VT"
    assert r.valori["fatturato"] == 1200000 and r.valori["data_costituzione"].year == 2019


def test_errori_di_validazione():
    intestazioni = ["Ragione sociale", "Partita IVA", "Regione", "Email"]
    mappa = imp.indovina_mappatura(intestazioni)
    r = imp.valida(3, ["", "12345", "Atlantide", "non-una-email"], intestazioni, mappa)
    imp.valida_azienda(r)
    testo = "; ".join(r.errori)
    assert "partita IVA non valida" in testo and "Atlantide" in testo
    assert "manca la ragione sociale" in testo and "indirizzo non valido" in testo
    assert imp.csv_errori([{"riga": 3, "errori": r.errori}]).startswith("﻿Riga;Errori")
