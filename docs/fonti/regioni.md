# Fonti regionali: Toscana, Lazio, Umbria, Marche

Regioni prioritarie indicate dal committente il 18/11/2026. Ogni portale è stato verificato a mano (formato, robots.txt, condizioni d'uso) prima di scriverne il codice.

## Riepilogo

| Regione | Fonte | Stato | Bandi attivi raccolti (18/11/2026) |
|---|---|---|---|
| Toscana | Sviluppo Toscana (`www.sviluppo.toscana.it`) | **Attiva** (`regione_toscana`) | 11 (10 aperti, 1 in arrivo) |
| Lazio | Lazio Innova (`www.lazioinnova.it`) + Lazio Europa (`www.lazioeuropa.it`) | **Attiva** (`regione_lazio`) | 34 (30 aperti, 4 in arrivo) |
| Umbria | Solo dataset nazionale | Nessuna fonte regionale strutturata | 9 regionali attivi nel dataset nazionale |
| Marche | Solo dataset nazionale | Portale protetto da un sistema anti-bot | 23 regionali attivi nel dataset nazionale |

Perché servono: il dataset nazionale non contiene ancora diversi bandi regionali recenti. Ad esempio "Bando Internazionalizzazione 2026" (Toscana, aperto dal 18/11) e "Voucher Internazionalizzazione PMI 2027" (Lazio, in arrivo) non ci sono.

## Regole comuni (`app/sources/regioni/comune.py`)
- Si raccolgono **solo i bandi che il portale dichiara aperti o in arrivo**: poche decine di richieste, una al secondo per dominio, con user agent identificabile.
- **Stato dichiarato** (`bando.stato_fonte`): si usa quando il portale non dà date strutturate. Le date, quando ci sono, prevalgono, tranne "chiuso".
- **Uscita dalla lista = chiuso** alla raccolta successiva (`assenze_per_scomparsa = 1`). Se il bando ricompare, viene riaperto.
- **Niente controllo dell'80%**: la lista cambia molto da un giorno all'altro. Il run si ferma se arrivano 0 bandi dove prima ce n'erano, o se la pagina non ha più la struttura attesa (`ErroreFormato`).
- **Reindirizzamento a un altro dominio** (tipicamente una verifica anti-bot): la fonte si ferma con un errore e non si tenta di aggirarlo.
- **Vocabolario comune**: le tassonomie dei portali sono tradotte nei valori del dataset nazionale (dimensioni, tipologie di soggetto, forma di agevolazione, finalità). I termini non riconosciuti sono ignorati e il campo resta "da verificare".
- **Condizioni d'uso**: nessuno dei portali pubblica una licenza o condizioni di riuso. Sono informazioni pubbliche di enti pubblici. Si usano solo internamente, con il link alla pagina ufficiale, senza ripubblicarle.

## Toscana: Sviluppo Toscana
- **Sito**: WordPress. `robots.txt` risponde 404, quindi nessun divieto.
- **Elenco**: `https://www.sviluppo.toscana.it/bandi/?_stato=aperto` (e `_stato=in-arrivo`), paginazione `_paged=N`. Sono i parametri FacetWP del portale.
- **Esclusioni**: tra gli "aperti" ci sono le fasi di **rendicontazione** e **varianti** (31 su 41 il 18/11). Riguardano solo chi ha già ottenuto il contributo e si escludono con `_fase=rendicontazione` e `_fase=varianti`.
- **Pagina del bando**: "Stato", "Domande dal", "Apertura" e "Chiusura" (es. "ore 16:00 del 18/12/2026"). Se il testo dice "esaurimento risorse", la nota lo riporta.
- **REST API**: `/wp-json/wp/v2/bando?slug=…` per le tassonomie `destinatario` e `tema`.
- **Non coperti**: i bandi pubblicati solo su `regione.toscana.it` (portale Liferay, che nel robots.txt vieta `asset_publisher`). Sono soprattutto bandi non per imprese. I bandi per imprese della Regione arrivano comunque dal dataset nazionale.

## Lazio: Lazio Innova + Lazio Europa
- **Siti**: entrambi WordPress, con `robots.txt` senza divieti.
- **Elenchi**: `/bandi/?stati=…`
  - Lazio Innova: `aperto` e `prossima apertura`;
  - Lazio Europa: `1` (aperto) e `2` (prossima apertura);
  - paginazione `/bandi/page/N/`.
- **Contenuti**: REST API `/wp-json/wp/v2/bandi?slug=…` (testo, `destinatari`, `tipologie-agevolazioni`, `tematiche` o `aree`, `enti`).
- **Doppioni**: molti bandi stanno su entrambi i portali con lo stesso slug. Si tiene la copia di Lazio Innova.
- **Date**: non strutturate, stanno nel testo. Lo stato è quello dichiarato dal portale e le date restano "da verificare" fino all'estrazione LLM (fase 3).
- Lazio Europa contiene anche bandi per scuole, cittadini ed enti. Si raccolgono comunque (il consulente può avere clienti non profit), poi il matching (fase 4) li filtra per tipologia di soggetto.

## Umbria
- `www.regione.umbria.it/la-regione/bandi` elenca soprattutto concorsi e avvisi, non bandi di finanziamento.
- Sviluppumbria (`/avvisi-pubblici`) mescola bandi, concorsi e avvisi immobiliari. La lista arriva via AJAX da una portlet Liferay e sono poche voci, spesso già presenti nel dataset nazionale (es. "STEP 2026").
- Le domande dei bandi FESR passano dalla piattaforma Gepafin.
- **Decisione**: per ora solo il dataset nazionale. Da rivalutare se il consulente segnala bandi umbri mancanti.

## Marche
- `bandi.regione.marche.it` e `www.regione.marche.it` reindirizzano un client automatico a una verifica anti-bot (Radware, `validate.perfdrive.com`).
- **Decisione**: non si aggira una protezione anti-bot. Restano il dataset nazionale (23 bandi regionali attivi) e, se servirà, una richiesta formale alla Regione di un open data o di un feed.
