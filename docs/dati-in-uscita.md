# Dati in uscita dal server

Elenco di tutti i dati che lasciano il server, verso chi e perché. Va aggiornato a ogni nuova integrazione.

| Destinatario | Quando | Cosa esce | Cosa NON esce mai | Stato |
|---|---|---|---|---|
| Incentivi Italia (`www.incentivitalia.gov.it`) | Raccolta notturna (02:30) | Solo una richiesta HEAD e il download del dataset pubblico, con user agent identificabile | Qualsiasi dato del CRM | **Attiva** |
| OpenAI: "Verifica connessione" | Solo quando il titolare preme il pulsante | La sola API key (elenco dei modelli disponibili, nessun token consumato) | Qualsiasi dato del CRM | **Attiva** |
| Server SMTP configurato: "Verifica connessione" | Solo quando il titolare preme il pulsante | Utente e password SMTP per il login; nessuna email inviata | Qualsiasi dato del CRM | **Attiva** |
| Siti degli enti (solo link diretti a PDF) | Raccolta | Richieste di download dei documenti pubblici dei bandi | Qualsiasi dato del CRM | Fase 2 |
| OpenAI (account del cliente): estrazione | Notte, bandi aperti o in arrivo cambiati | Solo il testo pubblico dei bandi | Dati delle aziende | Fase 3 |
| OpenAI: embeddings e scoring | Notte, matching | Dati anonimi: ATECO, regione e provincia, dimensione, anzianità, forma giuridica; descrizione, categorie e importo del progetto | Ragione sociale, P.IVA, codice fiscale, referenti, contatti, note, allegati | Fase 4 |
| OpenAI: bozze di aggiornamento | Generazione delle bozze | Dati dei bandi e dati anonimi dell'azienda; il testo torna con segnaposto (`{nome_referente}`, `{ragione_sociale}`) sostituiti sul server | Nomi e contatti | Fase 5 |
| Provider SMTP (da scegliere) | Email del mattino, avvisi, aggiornamenti approvati | Contenuto delle email e indirizzi dei destinatari | — | Fase 5 |
| WhatsApp Business Platform (Meta), se scelta | Aggiornamenti approvati | Numero del referente con consenso, testo del messaggio | — | Fase 5, da decidere |
| Storage dei backup (da scegliere) | Ogni notte | Dump del database e allegati, **cifrati con restic** prima di uscire | Dati in chiaro | Fase 7 |

## Regole
- Nessun invio a un destinatario non elencato qui.
- Una nuova integrazione si aggiunge a questa tabella **prima** di scriverne il codice.
- Nei test non si fa mai una chiamata di rete vera.
