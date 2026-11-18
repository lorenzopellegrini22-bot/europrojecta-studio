# Cose da migliorare

Aggiornato il 18/11/2026, dopo una nuova verifica su dati reali:
- **Bandi:** 6.095 da Incentivi Italia, più 45 attivi dalle fonti regionali di Toscana e Lazio.
- **Appalti:** 1.618 gare aperte da TED e 501 iniziative aperte Consip.
- **Test:** 150 automatici, tutti verdi.

## Fatto
- Pagina **Il mio account**:
  - cambio password, che disconnette gli altri dispositivi;
  - nuovi codici di recupero, confermati con il codice dell'app;
  - dispositivi collegati e ultimi accessi.
- **Pulizia notturna** (04:00) di sessioni scadute e log di accesso; **limite per IP** sul login.
- **Raccolta dei bandi ogni notte**: dataset nazionale più fonti regionali di Toscana e Lazio.
- **Pratiche a fasi**:
  - modelli, checklist con allegati, email con segnaposto e invio dal server;
  - scadenzario e promemoria;
  - export CSV e riepilogo stampabile.
- **Clienti:**
  - referenti, ATECO, dati dimensionali, segnalatori;
  - compilazione dal Registro Imprese (Openapi).
- **PDF dei bandi** (link diretti e allegati dei portali regionali), letti dall'AI;
- **Estrazione dei requisiti con l'AI** (fase 3):
  - citazioni verificate, regola manuale > fonte > AI;
  - pagina di revisione, budget e consumi.
- **Opportunità per cliente** ogni mattina (bandi e appalti, con punteggio e motivi) ed email delle novità;
- **Prefattibilità** con voci automatiche; **statistiche** dello studio.
- **Integrazioni:** interruttore attiva/disattiva e guida "i" per ognuna.
- **Appalti:**
  - fonti: TED e Consip ogni notte, import dei file ANAC (anche sotto soglia, con categorie SOA);
  - scheda gara completa e riassunto AI;
  - filtri per SOA, CPV, territorio, importo, scadenza e fonte.
- **Ricerca testuale** comune a tutte le pagine (vedi `docs/decisioni.md`):
  - parole in qualsiasi ordine, accenti e plurali ignorati, sinonimi;
  - una regione scritta nella ricerca diventa il filtro territoriale.

## Problemi trovati provando con i dati reali (già corretti)
- **Importi assurdi nella fonte** (oltre mille miliardi) bloccavano il salvataggio: ora vengono scartati e segnati "da verificare".
- **Job "ok" con una fonte in errore**: ora risulta "errore".
- **Enti locali trattati come nazionali**: 6 bandi di Comuni o Regioni che dichiarano "tutte le regioni" ora hanno il territorio "da verificare".
- **Importi "900.000"** letti come 900 euro.
- **Promemoria** che potevano partire due volte nello stesso giorno.
- **CIG ripetuti** nel file Consip.
- **Categorie SOA scritte senza trattino** ("OS18B").
- **Ricerca** con più parole, accenti o plurali (vedi sopra).

## Fatto il 18/11/2026 (verifica contro brief e preventivo)
- Punteggio AI delle opportunità, feedback e precisione per fascia.
- Doppioni tra fonte nazionale e regionale collegati; ricerca ordinata per pertinenza.
- CRM completo (progetti, attività, scadenze, de minimis, tag, consensi), import CSV,
  export completo, aggiornamenti ai clienti (email e WhatsApp), email del mattino completa,
  report PDF, comando di collaudo.
- Kit di produzione: Caddy, ClamAV, backup restic con prova di ripristino, rotazione della
  SECRET_KEY, avviso di scadenza della sessione. Tolto InfoAppalti.

## Da fare, in ordine di priorità
1. **Collaudo con il consulente:** etichettare insieme `docs/collaudo/set-proposto.json`
   (atteso compatibile / non compatibile / dubbio) ed eseguire `collaudo` (requisito del
   contratto: almeno 80% di precisione, zero esclusioni sbagliate).
2. **Prove con gli account veri del consulente** (non si possono fare senza le sue chiavi):
   - token Openapi in sandbox, poi in produzione;
   - casella SMTP: email del mattino e un aggiornamento di prova a sé stessi;
   - chiave OpenAI: estrazione di qualche bando e punteggio di 2-3 clienti, controllando i costi;
   - WhatsApp: modello approvato da Meta e un invio di prova (se scelto come canale);
   - giro completo nel browser con login e 2FA.
3. **Messa in produzione:** VPS in UE, dominio, storage esterno per restic
   (`docs/messa-in-produzione.md`). I file sono pronti e verificati in CI.
4. **Import reale:** chiedere al consulente l'export del CRM attuale e fare la prova a vuoto.
5. **PDF del dataset nazionale che non sono link diretti** (pagine dei ministeri): da
   valutare caso per caso. Fatti: OCR, bando-tipo, tabelle ISTAT (18/11/2026).
6. **Pubblicazione su Vercel**: senza worker né file persistenti mancano raccolte notturne,
   email del mattino, allegati, antivirus, PDF e OCR. Decidere se usare Docker su VPS.
7. **Categorie SOA per le gare TED**: oggi dal file ANAC importato o dal riassunto AI.
8. **Permessi dei collaboratori** da confermare: oggi non possono esportare, eliminare,
   importare né cambiare impostazioni e modelli.
9. Minori: verifica della connessione WhatsApp dalla pagina Integrazioni; catalogo MIT da
   ricontrollare quando torna online.
