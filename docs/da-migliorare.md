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

## Da fare, in ordine di priorità
1. **Punteggio delle opportunità con l'AI.** Oggi l'abbinamento è a regole: territorio, dimensione, ATECO, settore, SOA, CPV. Con l'estrazione dei requisiti (fase 3) si aggiungono similarità e motivazione scritta.
2. **Estrazione AI dei requisiti dei bandi (fase 3).** Molti campi sono ancora "da verificare" (regime, spese ammissibili, importi). Per il Lazio le date stanno solo nel testo.
3. **Doppioni tra fonte nazionale e regionale.** Lo stesso bando può comparire due volte; il collegamento arriva con la fase 3.
4. **Ordinamento per pertinenza** nella ricerca dei bandi. Oggi l'ordine è per scadenza; servirebbe prima il titolo che contiene le parole cercate.
5. **Categorie SOA per le gare TED.** Arrivano solo dal file ANAC importato (collegato per CIG) o dal riassunto AI. Si potrebbe leggere anche il disciplinare in PDF.
6. **Prove con gli account veri del consulente:**
   - token Openapi in sandbox, poi in produzione;
   - casella SMTP con email di prova;
   - chiave OpenAI con un riassunto;
   - giro completo nel browser con login e 2FA.
7. **Messa in produzione (fase 7):**
   - VPS con Caddy;
   - backup cifrati di database e volume `allegati`;
   - antivirus sugli allegati (es. ClamAV).
8. **InfoAppalti:** se il consulente ha un abbonamento, chiedere a InfoAppalti un'esportazione o un'API contrattuale. Con quella si integra; senza, no.
9. **Codici ISTAT dei comuni**, per il matching per sede e per i luoghi degli appalti ANAC.
10. **Permessi dei collaboratori** da confermare con il committente. Oggi possono tutto tranne modificare i modelli, le impostazioni ed eliminare dati.
11. Minori:
    - avviso prima della scadenza della sessione;
    - verifica WhatsApp;
    - rotazione della SECRET_KEY con MultiFernet;
    - catalogo MIT da ricontrollare quando torna online.
