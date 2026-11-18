# Dati in uscita dal server

Elenco di tutti i dati che lasciano il server, verso chi e perché. Va aggiornato a ogni nuova integrazione.

| Destinatario | Quando | Cosa esce | Cosa NON esce mai | Stato |
|---|---|---|---|---|
| Incentivi Italia (`www.incentivitalia.gov.it`) | Raccolta notturna (02:30) | Solo una richiesta HEAD e il download del dataset pubblico, con user agent identificabile | Qualsiasi dato del CRM | **Attiva** |
| Portali regionali: Sviluppo Toscana, Lazio Innova, Lazio Europa | Raccolta notturna (02:30) | Richieste GET di pagine pubbliche e della REST API pubblica di WordPress (1 al secondo, user agent identificabile) | Qualsiasi dato del CRM | **Attiva** |
| OpenAI: "Verifica connessione" | Solo quando il titolare preme il pulsante | La sola API key (elenco dei modelli disponibili, nessun token consumato) | Qualsiasi dato del CRM | **Attiva** |
| Server SMTP configurato: "Verifica connessione" | Solo quando il titolare preme il pulsante | Utente e password SMTP per il login; nessuna email inviata | Qualsiasi dato del CRM | **Attiva** |
| TED, Tenders Electronic Daily (`api.ted.europa.eu`) | Raccolta notturna degli appalti (03:15) o pulsante "Aggiorna adesso" | Solo la richiesta di ricerca pubblica (paese Italia, bandi aperti), con user agent identificabile | Qualsiasi dato del CRM | **Attiva** |
| Consip, open data (`dati.consip.it`) | Raccolta notturna degli appalti (03:15) | Una richiesta GET del file pubblico «Bandi e gare» dell'anno, con user agent identificabile (robots.txt rispettato) | Qualsiasi dato del CRM | **Attiva** |
| OpenAI: riassunto di un appalto | Solo quando un utente preme «Riassumi con l'AI» | La scheda pubblica della gara (oggetto, ente, importi, date, categorie, descrizione) | Qualsiasi dato dei clienti | **Attiva** (se collegata) |
| Server SMTP del consulente: opportunità | Ogni mattina (07:00) se ci sono novità; «Segnala al cliente» solo su richiesta | Agli utenti dello studio: elenco delle opportunità per cliente. Al referente del cliente: titolo, scadenza e link del bando o della gara | Dati di altri clienti | **Attiva** (se collegato) |
| Openapi, Registro Imprese (`company.openapi.com`, sandbox `test.company.openapi.com`) | Solo quando un utente preme "Compila dal Registro Imprese" o "Verifica connessione", e solo se l'integrazione è attiva | La partita IVA o il codice fiscale cercati e il token del consulente. Ogni ricerca in produzione ha un costo, addebitato al conto Openapi del consulente | Nomi, referenti, note, pratiche, qualsiasi altro dato del CRM | **Attiva** (se collegata) |
| Server SMTP del consulente: invio dalla pratica | Solo quando un utente preme "Invia email" su una comunicazione | Destinatario (email del referente), oggetto e testo della comunicazione, compilati con i dati della pratica | Allegati, note interne, dati di altri clienti | **Attiva** (se collegato) |
| Server SMTP del consulente: promemoria | Ogni mattina alle 07:30, se ci sono scadenze | Agli utenti dello studio: elenco delle proprie operazioni scadute o in scadenza (titolo, cliente, codice pratica) | — (destinatari solo interni) | **Attiva** (se collegato) |
| Siti degli enti (solo link diretti a PDF) | Ogni notte (04:05), al massimo 60 PDF, solo bandi aperti o in arrivo | Richieste GET dei PDF pubblici dei bandi e del robots.txt del sito (rispettato), con user agent identificabile e una richiesta al secondo per dominio | Qualsiasi dato del CRM | **Attiva** |
| OpenAI (account del cliente): estrazione | Ogni notte (04:30) o «Esegui adesso», solo bandi aperti o in arrivo nuovi o cambiati, entro il budget | Solo il testo pubblico dei bandi (titolo, ente, descrizione, campi descrittivi della fonte) | Dati delle aziende e dei clienti | **Attiva** (se collegata) |
| OpenAI: embeddings e punteggio delle opportunità | Ogni mattina (07:00) dopo l'abbinamento, entro il budget e il limite giornaliero; solo opportunità nuove o cambiate (hash) | Profilo anonimo: forma giuridica, dimensione, regione e provincia, ATECO, dipendenti, fatturato, SOA, regioni e categorie CPV di interesse; scheda pubblica del bando o della gara | Ragione sociale, P.IVA, codice fiscale, referenti, email, PEC, indirizzi, note, allegati | **Attiva** (se collegata) |
| OpenAI: bozze di aggiornamento | Generazione delle bozze | Dati dei bandi e dati anonimi dell'azienda; il testo torna con segnaposto (`{nome_referente}`, `{ragione_sociale}`) sostituiti sul server | Nomi e contatti | Fase 5 |
| Provider SMTP (da scegliere) | Email del mattino, avvisi, aggiornamenti approvati | Contenuto delle email e indirizzi dei destinatari | — | Fase 5 |
| WhatsApp Business Platform (Meta), se scelta | Aggiornamenti approvati | Numero del referente con consenso, testo del messaggio | — | Fase 5, da decidere |
| Storage dei backup (da scegliere) | Ogni notte | Dump del database e allegati, **cifrati con restic** prima di uscire | Dati in chiaro | Fase 7 |

## Regole
- Nessun invio a un destinatario non elencato qui.
- Una nuova integrazione si aggiunge a questa tabella **prima** di scriverne il codice.
- Nei test non si fa mai una chiamata di rete vera.
