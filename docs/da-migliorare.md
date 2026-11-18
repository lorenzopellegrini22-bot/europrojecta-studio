# Cose da migliorare (dalla prova del 18/11/2026)

Prova eseguita sull'app vera (browser + PostgreSQL 16 con pgvector):
- primo accesso con configurazione della 2FA e codici di recupero;
- salvataggio della chiave OpenAI e verifica reale (chiave finta rifiutata correttamente da OpenAI);
- pagina SMTP, vista su telefono, logout.

Test automatici: 43 verdi in locale e in CI.

## Già corretto durante la prova
- Etichetta "Rimuovi api key salvata" (tutto minuscolo) → "Rimuovi il valore salvato di «API key»".
- Su telefono il nome "Europrojecta Studio" andava a capo.
- L'estensione `unaccent` non era usata: tolta dalla prima migrazione, si aggiunge con la ricerca testuale.

## Da fare, in ordine di priorità
1. **Pagina "Il mio account"**: cambio password, rigenerazione dei codici di recupero, elenco delle sessioni attive con "esci da tutti i dispositivi", ultimi accessi da `log_accesso`. Oggi si fa solo da riga di comando.
2. **Pulizia periodica nel worker**: le sessioni scadute si cancellano solo se qualcuno le riusa. Serve un job notturno che elimini sessioni scadute e log più vecchi di N mesi.
3. **Limite di richieste per IP sul login**: oggi il blocco è per account (5 tentativi). Chi prova molte email diverse non viene rallentato.
4. **Avviso alla scadenza della sessione**: se la sessione scade mentre si compila un form, i dati inseriti si perdono. Serve un avviso prima della scadenza, oppure la conservazione della bozza.
5. **Stato delle integrazioni in home**: un riquadro "OpenAI non configurata / verifica fallita", così il consulente lo vede senza entrare nelle impostazioni (arriva con la dashboard, fase 5).
6. **Verifica WhatsApp**: si aggiunge con il canale WhatsApp (fase 5), dopo aver verificato la documentazione Meta.
7. **Rotazione della SECRET_KEY**: oggi cambiarla obbliga a reinserire le chiavi e riconfigurare la 2FA. Con MultiFernet si può ruotare senza perdere nulla.
8. **Ambiente locale senza Docker**: per provare l'app in locale si usa `pgserver` (Postgres 16 + pgvector via pip). Va documentato nel README, oppure Docker va reinstallato quando c'è spazio su disco.
