# Cose da migliorare

Aggiornato il 18/11/2026, dopo la prova completa su dati reali:
- raccolta reale da Incentivi Italia: 6.095 bandi, di cui 749 aperti e 12 in arrivo;
- verifica nel browser di dashboard, bandi, scheda bando, fonti, account e impostazioni;
- 90 test automatici verdi.

## Fatto
- Pagina **Il mio account**: cambio password (disconnette gli altri dispositivi), nuovi codici di recupero confermati con il codice dell'app, dispositivi collegati, ultimi accessi.
- **Pulizia notturna** (04:00): sessioni scadute e log di accesso più vecchi di un anno.
- **Limite per indirizzo IP** sul login: 20 tentativi falliti in 15 minuti, su qualsiasi account. Dietro Caddy si legge l'IP reale (`--proxy-headers`).
- **Stato delle integrazioni e delle fonti in home**: avvisi se OpenAI o SMTP non sono collegati, o se una fonte fallisce da 2 raccolte.
- Raccolta dei bandi **pianificata ogni notte** nel worker, con un test che lo verifica.

## Problemi trovati provando con i dati reali (già corretti)
- Importi assurdi nella fonte (oltre mille miliardi) mandavano in errore il salvataggio: ora vengono scartati e segnalati come "da verificare".
- Il job risultava "ok" anche se una fonte era in errore: ora risulta "errore".
- 6 bandi di enti locali (Comuni, Regioni) dichiarano "tutte le regioni": non vengono più trattati come nazionali, il territorio è "da verificare".
- In alcuni record più comuni sono in un'unica voce separata da ";": ora vengono separati.
- La dashboard contava come "nuovi" tutti i bandi della prima importazione: ora conta solo quelli comparsi dopo.

## Da fare, in ordine di priorità
1. **Copertura regionale (fase 2, promessa al cliente)**: servono le regioni dei clienti del consulente. Il dataset nazionale contiene già 545 bandi regionali e 139 comunali attivi, ma va confrontato con i portali delle regioni prioritarie (es. il Lazio ne ha solo 18 propri).
2. **Codici ISTAT dei comuni**: oggi i comuni dei bandi sono nomi testuali. Servono la tabella ISTAT e la normalizzazione per il matching per sede.
3. **Avvisi email sulle fonti** dopo 2 fallimenti consecutivi: il dato è già contato, l'invio arriva con l'SMTP (fase 5).
4. **Avviso alla scadenza della sessione**: se la sessione scade mentre si compila un form, i dati inseriti si perdono.
5. **Verifica WhatsApp**: con il canale WhatsApp (fase 5), dopo aver verificato la documentazione Meta.
6. **Rotazione della SECRET_KEY** con MultiFernet.
7. **Ricerca testuale migliore** (accenti, più parole): con `unaccent` e un indice trigram quando i dati crescono.
