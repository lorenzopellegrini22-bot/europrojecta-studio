# Fonte: Commissione europea — portale Funding & Tenders

Verifica del 23/11/2026. Codice: `app/sources/ue.py`, codice fonte `ue_funding_tenders`.

## Cosa si raccoglie
- I **topic** (inviti a presentare proposte, type 1) con stato aperto o in arrivo, dall'API di
  ricerca che usa il portale stesso: `POST https://api.tech.ec.europa.eu/search-api/prod/rest/search`
  con `apiKey=SEDIA` (valore usato dal portale, non documentato: se cambia, la raccolta fallisce e
  compare tra le fonti in errore).
- Esclusi: gare EuropeAid (type 2, per Paesi extra UE) e bandi «a cascata» (type 8, spesso per
  un solo Paese, es. PMI ungheresi).
- Esclusi i topic con tutte le scadenze passate (il portale ne lascia molti «aperti»).
- Il 23/11/2026: 600 voci aperte, 149 topic aperti con scadenza futura, 376 con quelli in arrivo.

## Condizioni d'uso
- `api.tech.ec.europa.eu/robots.txt` non esiste (404); `ec.europa.eu/robots.txt` non vieta il
  portale.
- Nota legale della Commissione (commission.europa.eu/legal-notice_en): riutilizzo consentito
  citando la fonte e indicando le modifiche (Decisione 2011/833/UE). La scheda del bando cita
  la fonte.
- Circa 11 richieste a notte, una al secondo.

## Dati ricavati
- Titolo (in inglese), programma, invito, tipo di azione, contributo UE per progetto
  (min-max) e dotazione del topic da `budgetOverview`, scadenze (la più vicina come chiusura,
  le altre nella nota), descrizione e **condizioni e criteri di valutazione** (`topicConditions`:
  ammissibilità, Paesi, criteri di assegnazione, forma del contributo).
- Beneficiari: il portale non li dà strutturati. Si ricavano dal programma (CERV, LIFE,
  protezione civile, AMIF → enti; Europa digitale, Mercato unico, FEAMPA → imprese; ERC, Euratom,
  difesa, dottorati → solo ricerca) e dal titolo; il testo conta solo se ne parla tre volte.

## Abbinamento (app/opportunita/abbinamento.py)
- Casella «Bandi europei» nella scheda del cliente: predefinita per gli enti, non per le imprese.
- −10 per tutti: testi in inglese, di solito in partenariato con enti di altri Paesi.
- Imprese: serve un progetto d'investimento collegato dal titolo o dall'invito (parole inglesi
  per categoria di spesa); Horizon solo con un progetto di ricerca e sviluppo.
- Enti: servono gli enti tra i beneficiari; Horizon sotto la soglia per i comuni sotto 5.000
  abitanti.
