from app.documenti.servizio import brani_utili
from app.extraction import bando_tipo


def _bando_conforme() -> str:
    corpo = {
        1: "Il presente bando sostiene la digitalizzazione delle imprese della Regione Lazio.",
        2: "La dotazione finanziaria è pari a euro 5.000.000.",
        3: "La gestione è affidata a Lazio Innova S.p.A.",
        4: "Possono beneficiare le micro, piccole e medie imprese con sede operativa nel Lazio.",
        5: "Sono agevolabili i progetti di digitalizzazione dei processi aziendali.",
        6: "Sono ammissibili le spese per software, hardware e consulenze specialistiche.",
        7: "Contributo a fondo perduto del 50% in regime de minimis (Reg. UE 2023/2831).",
        8: "Le domande si presentano a sportello dal 1° febbraio 2027.",
        11: "Le agevolazioni sono revocate in caso di " + "inadempimento grave. " * 400,
        12: "Sono disposti controlli a campione. " * 300,
        14: "I dati personali sono trattati secondo il GDPR. " * 300,
        15: "Per quanto non previsto si applica il Codice degli incentivi.",
    }
    righe = []
    for n, testo in corpo.items():
        intestazione = bando_tipo.PARTIZIONI[n]
        righe.append(f"Art. {n} - {intestazione}" if n % 2 else f"{n}. {intestazione.upper()}")
        righe.append(testo)
    return "\n".join(righe)


def test_partizioni_riconosciute():
    testo = _bando_conforme()
    p = bando_tipo.partizioni(testo)
    assert {1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15} == set(p)
    assert "micro, piccole e medie" in p[4] and bando_tipo.conforme(testo)
    assert not bando_tipo.conforme("1. Finalità\n2. Dotazione finanziaria\n3. Destinatari")


def test_pdf_lungo_conforme_tiene_i_requisiti():
    testo = _bando_conforme()
    assert len(testo) > 20_000
    ridotto = brani_utili(testo, 3000)
    for frase in ("micro, piccole e medie", "software, hardware", "regime de minimis", "sportello"):
        assert frase in ridotto
    assert "dati personali" not in ridotto and len(ridotto) <= 3000
    # l'ordine originale del bando è mantenuto
    assert ridotto.index("SOGGETTI BENEFICIARI") < ridotto.index("SPESE AMMISSIBILI")
