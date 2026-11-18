# Fonte: Incentivi Italia (verifica del 18/11/2026)

| Punto | Esito della verifica |
|---|---|
| Dominio | `www.incentivi.gov.it` → 302 → `https://www.incentivitalia.gov.it` |
| CSV | `https://www.incentivitalia.gov.it/open-data/incentivi/OpenData_Incentivi.csv`, 19.089.104 byte, UTF-8 senza BOM |
| JSON | `https://www.incentivitalia.gov.it/open-data/incentivi/OpenData_Incentivi.json`, 28.741.365 byte, array di oggetti |
| Aggiornamento | **settimanale** (dichiarato nella pagina open data); `Last-Modified: Thu, 12 Nov 2026 04:02:05 GMT` |
| Licenza | CC BY 4.0: citare la fonte, link alla licenza, indicare che i dati sono rielaborati |
| robots.txt | Non vieta `/open-data`; vieta `/search`, `/admin`, `/user/*` e simili |
| Download | **Troncato più volte dal server** (200 con meno byte del Content-Length). `Accept-Ranges: bytes`: la ripresa con `Range` funziona (206) |

## Contenuto (18/11/2026)
- 6.095 record, 51 campi, `ID_Incentivo` e `idBando` presenti in tutti i record.
- Stato calcolato: 749 aperti, 12 in arrivo, 5.315 chiusi, 19 senza date. Dei 780 aperti, in arrivo o senza data di chiusura, 223 non hanno data di chiusura.
- Date in formato ISO `AAAA-MM-GG`.
- Nel JSON sono liste: `Regioni`, `Comuni`, `Dimensione_beneficiari`, `Forma_agevolazione`, `Tipologia_Soggetto`, `Settore_Attivita`, `Ambito_territoriale`. Gli importi sono numeri.

## Copertura territoriale (bandi aperti, in arrivo o senza data di chiusura)
- 79 nazionali (19 o più regioni) e 701 locali.
- Dei 701 locali, 437 sono concessi da una Regione o Provincia autonoma e 84 da Camere di Commercio.
- Bandi locali per regione: Trentino-Alto Adige 128, Lombardia 95, Friuli-Venezia Giulia 75, Emilia-Romagna 50, Veneto 45, Sardegna 40, Toscana 38, Calabria 34, Puglia 33, Valle d'Aosta 27, Sicilia 27, Piemonte 24, Marche 24, **Lazio 18**, Abruzzo 18, Campania 15, Molise 15, Liguria 13, Umbria 12, Basilicata 10.

## Particolarità da gestire nel parser
- `Ambito_territoriale` **non** è nazionale o regionale: contiene zone speciali (Aree interne, ZES, Zone sismiche, Zone franche, Emergenza climatica, Non applicabile). A volte sono più valori uniti da virgola in un'unica stringa.
- `Comuni = [" "]` in 2.827 record: va trattato come `NULL`.
- `Spesa_Ammessa_Min` e importi simili valgono spesso `0`: va trattato come "non indicato".
- `Codici_ATECO`:
  - 4.297 record "Tutti i settori economici ammissibili…": nessun vincolo;
  - 848 con prefisso `ATECO 2025:` e 131 con `ATECO 2007:`; i codici sono separati da `;`.
- `Dimensione_beneficiari` include "Non classificabile/classificato" e "Grande Impresa".
- `Descrizione` è strutturata in "Cos'è / A chi si rivolge / Cosa prevede", senza separatori affidabili.
- `Note_di_apertura_chiusura` contiene testo libero (es. "In attesa dell'emissione del bando specifico…").
