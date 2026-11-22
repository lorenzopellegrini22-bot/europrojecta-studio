# ANAC – Piattaforma di Pubblicità a Valore Legale (bandi di gara, anche sotto soglia)

Verifica del 22/11/2026. Codice: `app/appalti/pvl.py`, nel job notturno `raccolta_appalti`
subito dopo TED.

## Perché
Dal 1/1/2024 la pubblicità legale dei bandi di gara, anche sotto le soglie europee, passa dalla
Banca Dati Nazionale dei Contratti Pubblici (BDNCP) di ANAC e si consulta sulla piattaforma
pubblica <https://pubblicitalegale.anticorruzione.it>, senza credenziali. Prima l'app aveva:
- TED: solo gare sopra soglia, senza categorie SOA;
- i file open data ANAC (dati.anticorruzione.it): il portale rifiuta lo scarico dal server,
  quindi vanno scaricati dal browser e importati a mano (pagina Appalti → Importa da ANAC).
Il 22/11/2026 sul sito non c'era nessuna gara ANAC: gli affidamenti sotto soglia mancavano del
tutto e il filtro per categoria SOA dava sempre 0.

## Come si legge
Gli stessi indirizzi che usa la pagina pubblica «Bandi e avvisi di indizione»:
- elenco: `GET /api/v0/avvisi?dataPubblicazioneStart=gg/mm/aaaa&dataPubblicazioneEnd=…&codiceScheda=2,4&page=N&size=100`
  (2,4 = bandi e avvisi di indizione; il 20/11/2026: 131 avvisi in un giorno);
- dettaglio: `GET /api/v0/avvisi/{idAvviso}`: ha il codice CPV («55510000_Servizi di mensa»),
  mentre l'elenco ne ha solo la descrizione.
Il sito non ha un robots.txt (risponde con la pagina dell'applicazione) e risponde in JSON a un
programma che si presenta con il proprio nome; si rispettano le pause di almeno un secondo tra
le richieste.

Campi usati: committente (`soggetti_sa`), oggetto (`metadata.titolo`/`descrizione`), lotti
(`SEZ. C - Oggetto`: CIG, natura, CPV, valore stimato, `luogo_nuts` = provincia, `luogo_istat`
= comune, termine, link ai documenti, `categorie` SOA come «OG 1», «OS 18-B»), scadenza
(`dataScadenza`), pubblicazione, link all'avviso su TED per le gare sopra soglia.

## Scelte
- Gare sopra soglia (con il link a TED): non si duplicano; alla gara TED si aggiungono CIG e
  categorie SOA. Se TED non l'ha ancora, la porterà TED.
- Gare sotto soglia: una gara «anac» per avviso (`pvl-<idAppalto>`), con tutti i lotti; le
  rettifiche aggiornano la stessa gara. Se la gara era stata importata dal file ANAC (stesso
  CIG) si aggiorna quella.
- Solo gare con la scadenza non ancora passata.
- Provincia e regione da `luogo_nuts`; se manca (circa 4 avvisi su 10), dal comune con la
  tabella ISTAT dell'app.
- La piattaforma non dice quale categoria SOA è la prevalente: l'abbinamento la tratta come
  «categoria prevalente non indicata». L'import dei file ANAC resta utile per saperlo.
- Prima esecuzione: 60 giorni di pubblicazioni (al massimo 1.500 dettagli per notte, il resto
  le notti dopo); poi ogni notte gli ultimi 3 giorni.

Dati: ANAC, BDNCP (licenza CC BY-SA 4.0 degli open data ANAC). Fa fede l'avviso pubblicato.
