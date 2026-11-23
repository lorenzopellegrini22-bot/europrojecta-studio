# Registro delle decisioni

Ogni voce: data, decisione, motivo. Le decisioni dello stack prese nel brief iniziale sono riassunte in fondo.

## 2026-11-18 — Nome del prodotto: Europrojecta Studio
Il gestionale si chiama **Europrojecta Studio**. Repository `europrojecta-studio`, pacchetto Python `app`.

## 2026-11-18 — Fonte nazionale: formato JSON invece del CSV
**Motivo:** nel JSON i campi multivalore sono già liste e i numeri sono numeri. Il CSV usa `\,` dentro le virgolette e richiederebbe un parser dedicato. Dimensione: JSON circa 28 MB, CSV circa 19 MB, accettabile.
Dettagli in `docs/fonti/incentivi-italia.md`.

## 2026-11-18 — Download robusti: ripresa con Range e controllo della dimensione
**Motivo:** durante la verifica il server ha troncato più volte il download (8,6 MB e 4,4 MB su 19 MB) con risposta 200. Il downloader riprende con `Range` (il server risponde 206) finché i byte non corrispondono a `Content-Length`, poi verifica che il JSON sia completo. Il controllo di sanità dell'80% resta come ulteriore protezione.

## 2026-11-18 — Il dataset nazionale si aggiorna ogni settimana, non ogni giorno
**Motivo:** lo dichiara la pagina open data, e `Last-Modified` lo conferma (12/11 letto il 18/11).
**Conseguenze:**
- un bando è "scomparso" se manca in **2 versioni consecutive del file**, non in 2 giorni;
- se `Last-Modified`/ETag non cambiano, la raccolta finisce subito con esito "invariato";
- il controllo dell'80% confronta con l'ultima versione elaborata.

## 2026-11-18 — Territorio: dal campo `Regioni`, non da `Ambito_territoriale`
**Motivo:** `Ambito_territoriale` contiene zone speciali (Aree interne, ZES, Zone sismiche, Zone franche, Emergenza climatica), non la scala nazionale o regionale. Il territorio si ricava dall'elenco `Regioni`: con 19 o più regioni il bando è nazionale. Le zone speciali vanno in `bando_requisiti.aree_speciali[]` e, finché non si classificano i comuni, generano un requisito "da verificare".

## 2026-11-18 — Embeddings: colonna `vector` senza dimensione fissa
**Motivo:** con meno di mille bandi e qualche centinaio di progetti la ricerca esatta è istantanea, quindi non serve un indice ANN (che richiederebbe una dimensione fissa). Cambiare modello di embeddings non richiede una migrazione: si ricalcola tutto e si controlla nel codice che la dimensione sia coerente con `embedding_modello`.

## 2026-11-18 — Liste vuote vietate nel database
**Motivo:** in `bando_requisiti`, `NULL` vuol dire "nessun vincolo noto" e una lista vuota non deve mai voler dire "nessuno ammesso". Un check constraint (`cardinality(col) > 0`) rende impossibile l'ambiguità.

## 2026-11-18 — Integrazione: un database temporaneo per ogni esecuzione dei test
**Motivo:** esecuzioni parallele (in locale e in CI) non devono disturbarsi. `TEST_DATABASE_URL` punta a un database di servizio. Se manca, i test di integrazione vengono saltati, ma in CI è sempre impostata.

## 2026-11-18 — Repository privato su GitHub con CI GitHub Actions
La CI esegue ruff, pytest su `pgvector/pgvector:pg16` e la build dell'immagine Docker.

## 2026-11-18 — Pagina Impostazioni → Integrazioni per tutte le chiavi API
**Richiesta del committente:** la piattaforma deve avere impostazioni da cui collegare tutte le chiavi API necessarie.
**Decisione:**
- Una tabella generica `integrazione`, una riga per servizio: OpenAI, SMTP, WhatsApp Business.
- Campi normali in chiaro (`config`); segreti cifrati uno per uno (`segreti`), con l'anteprima delle ultime 4 cifre (`anteprime`).
- L'API key di OpenAI sta qui, non in `impostazioni_ai`. In `impostazioni_ai` (fase 3) restano modelli, budget e prezzi.
- Ordine di precedenza: valore salvato dall'app > variabile d'ambiente > predefinito.
- Un campo segreto lasciato vuoto mantiene la chiave salvata; per cancellarla c'è la casella "Rimuovi".
- "Verifica connessione":
  - OpenAI: elenca i modelli, una chiamata che non consuma token;
  - SMTP: connessione, TLS e login, senza inviare email;
  - WhatsApp: arriva con il canale in fase 5.
- Solo il titolare vede e modifica le integrazioni. L'audit registra quali campi cambiano, mai i valori dei segreti.
**Motivo:** un solo punto di configurazione, comprensibile per il consulente. Aggiungere un servizio vuol dire descriverlo in `app/integrazioni/registro.py` e allargare il check constraint.

## 2026-11-18 — Cifratura dei segreti: Fernet con chiave derivata via HKDF da SECRET_KEY
- Libreria `cryptography`, Fernet (AES-128-CBC + HMAC-SHA256).
- Una chiave diversa per ogni scopo (`integrazione:openai:api_key`, `totp`, …), derivata con HKDF-SHA256.
- Token di sessione e codici di recupero non si cifrano: si salva solo un'impronta HMAC con chiave derivata, quindi non sono rileggibili.
- **Rischio:** se `SECRET_KEY` cambia, i segreti salvati non si decifrano più. La pagina lo segnala ("Da reinserire") e la 2FA si azzera con `python -m app.cli reimposta-2fa`. Per una futura rotazione delle chiavi servirà MultiFernet.

## 2026-11-18 — Accesso anticipato dalla fase 1
La pagina delle chiavi non può essere pubblica, quindi il login è stato fatto subito:
- argon2id;
- TOTP obbligatorio con QR code generato sul server (segno, SVG inline, nessuna risorsa esterna);
- 10 codici di recupero monouso;
- blocco di 15 minuti dopo 5 tentativi falliti (password o codice);
- sessioni lato server con scadenza per inattività (60 min) e assoluta (12 h), nuovo token dopo la 2FA;
- token CSRF più controllo di Origin/Referer su ogni richiesta che modifica dati;
- CSP senza risorse esterne.
Un codice TOTP non si può riusare: si salva l'ultimo intervallo usato.
Gli utenti si creano con `python -m app.cli crea-utente`.

## 2026-11-18 — Docker non installato sul PC di sviluppo
Il disco C: si è riempito durante la build dell'immagine. Docker Desktop è stato disinstallato su indicazione del committente. I test di integrazione (Postgres + pgvector) girano nella CI di GitHub. In locale vengono saltati se manca `TEST_DATABASE_URL`, oppure si possono puntare a un Postgres raggiungibile.

## 2026-11-18 — Raccolta dei bandi: pianificazione e regole
- **Pianificazione:** job `raccolta_bandi` nel worker, ogni notte alle `RACCOLTA_ORARIO` (default 02:30, Europe/Rome).
  - Se il worker era spento, recupera entro 6 ore (`misfire_grace_time`); più esecuzioni perse diventano una sola (`coalesce`).
  - All'avvio, se nessuna raccolta è mai riuscita, ne parte subito una.
  - Advisory lock di Postgres e una riga in `job_run` per ogni esecuzione.
  - Si può lanciare anche a mano: `python -m app.cli raccogli-bandi` oppure il pulsante in Fonti.
- **File invariato:** la fonte non supporta ETag né richieste condizionali (risponde sempre 200). Si fa prima una HEAD e si confronta `Last-Modified` con l'ultima versione elaborata.
- **Scomparsa:** un bando viene chiuso se manca in 2 versioni consecutive del file. Se ricompare viene riaperto.
- **ID reimpostati:** un record con un ID nuovo ma lo stesso `idBando` (RNA) di un bando non più presente viene riconciliato, invece di creare un duplicato.
- **Importi:** 0 significa "non indicato". Oltre 100 miliardi di euro è un refuso: il valore viene scartato e segnalato come da verificare.
- **Enti locali "nazionali":** se l'ente è un Comune, una Regione, una Provincia o una Camera di Commercio e la fonte elenca tutte le regioni, il territorio diventa "sconosciuto" (da verificare), non "nazionale".
- **Ricalcolo:** `raccogli-bandi --forza` riscarica il file e ricalcola i requisiti di tutti i bandi. Serve dopo una correzione del parser.
- **Requisiti:** per ora `bando_requisiti` contiene solo i dati della fonte. In fase 3 la risoluzione completa (correzione manuale > fonte > LLM) sostituirà la scrittura diretta. `regime`, `modalita` e `categorie_spesa` restano NULL e "da verificare" finché non c'è l'estrazione LLM.

## 2026-11-18 — Sviluppo locale senza Docker: pgserver
Per provare l'app sul PC di sviluppo si usa `pgserver` (PostgreSQL 16.2 + pgvector 0.6.2 installabili via pip), in un ambiente separato dal progetto. Non include `unaccent`: è un motivo in più per aggiungere quell'estensione solo quando servirà, verificando che ci sia nell'immagine di produzione. In CI e in produzione resta `pgvector/pgvector:pg16`.

## 2026-11-18 — Fonti regionali: Toscana e Lazio attive, Umbria e Marche dal dataset nazionale
**Richiesta del committente:** priorità a Toscana, Lazio, Umbria e Marche.
**Decisione** (dettagli in `docs/fonti/regioni.md`):
- Toscana da Sviluppo Toscana; Lazio da Lazio Innova + Lazio Europa, senza doppioni. In entrambi i casi elenchi filtrati per stato e REST API standard di WordPress.
- Umbria: nessuna fonte regionale strutturata per i bandi di finanziamento, quindi solo il dataset nazionale.
- Marche: portale protetto da un sistema anti-bot, che non si aggira. Solo il dataset nazionale.
- Nuova colonna `bando.stato_fonte`: lo stato dichiarato dal portale vale quando mancano le date.
- Per le fonti regionali si raccolgono solo i bandi aperti o in arrivo. L'uscita dalla lista chiude il bando. Al posto del controllo dell'80% ci sono controlli sulla struttura delle pagine.
- I doppioni tra fonte nazionale e regionale restano due bandi distinti, ciascuno con la sua fonte. Il collegamento (`bando_canonico_id`) arriva con l'estrazione LLM (fase 3), che confronta titolo, ente e date.
- Nuova dipendenza: `beautifulsoup4`, con il parser `html.parser` della libreria standard.

---

## 2026-11-18 — Pratiche a fasi con modelli, clienti essenziali, scadenzario
**Richiesta del committente:** avere le funzioni di gestione pratiche viste in un prodotto concorrente (modelli a fasi, checklist, comunicazioni, scadenzario). Non era nel preventivo: lo schema le indicava come "rinviate a decisione".
**Decisione:**
- `modello_pratica` → `modello_fase` → `modello_operazione`. L'operazione ha un tipo: attività, checklist documenti, dati da raccogliere o comunicazione al cliente (oggetto e testo con segnaposto). Ha anche una scadenza relativa: giorni dall'inizio della fase.
- All'apertura la pratica **copia** fasi e operazioni (`pratica_fase`, `pratica_operazione`, `pratica_documento`, `pratica_dato`). Si può personalizzare senza toccare il modello, e cambiare il modello non tocca le pratiche già aperte.
- Lo stato della pratica (individuato … chiusa, dallo schema) è separato dalla fase in corso. Ogni cambio finisce nel diario (`pratica_evento`).
- Comunicazioni: per ora anteprima con i dati veri, pulsante "Copia" e apertura nel programma di posta (`mailto:`). Nessun invio dal server finché il canale email non è pronto (fase 5).
- Scadenzario: operazioni da fare con scadenza e presentazioni delle domande, raggruppate per giorno, con filtro "solo le mie".
- Export per Excel in CSV (`;` e BOM UTF-8), senza nuove dipendenze.
- Clienti: sottoinsieme di `azienda`, `sede` e `referente` dello schema F1, più contatti di fatturazione (PEC, SDI). Il resto del CRM (ATECO, de minimis, consensi, import CSV) arriverà con nuove colonne.
- Permessi: titolare e collaboratori gestiscono clienti e pratiche. Solo il titolare modifica i modelli ed elimina clienti e pratiche.
- 3 modelli di esempio scritti da zero (fondo perduto, voucher a sportello, finanziamento agevolato), da caricare con un clic o con `python -m app.cli modelli-esempio`.

## 2026-11-18 — Integrazioni attivabili, guida "i" e Registro Imprese (Openapi)
**Richiesta del committente:** compilare il cliente dal Registro Imprese, con la possibilità di attivarlo o disattivarlo dalle impostazioni e una guida al collegamento.
**Decisione:**
- Ogni integrazione ha un interruttore (`integrazione.attiva`). Una disattivata resta configurata ma l'app non la usa.
- Ogni integrazione ha una guida passo passo (pulsante "i") con i soli link ufficiali, verificati uno per uno.
- **Fornitore: Openapi S.p.A., servizio "Company".** Specifica OpenAPI ufficiale verificata: endpoint `IT-start` e `IT-advanced`, token Bearer, produzione `company.openapi.com` e sandbox `test.company.openapi.com`.
- **Costi:** ogni ricerca in produzione costa, quindi l'integrazione parte **disattivata** e in **sandbox**. Anche "Verifica connessione" fa una ricerca vera (in sandbox è gratuita).
- **Compilazione del cliente:** riempie solo il form e niente viene salvato finché l'utente non preme Salva. Ogni ricerca finisce nell'audit (azione `invia`).
- **ATECO:** si usa la classificazione 2007 restituita da Openapi, l'unica con una versione esplicita. Viene salvata come ATECO primario (`azienda_ateco`).

## 2026-11-18 — Allegati, invio email, promemoria, segnalatori, prefattibilità, statistiche
- **Allegati:**
  - salvati su disco con un nome casuale (`CARTELLA_ALLEGATI/AAAA/MM/`), con tipi ammessi in whitelist e limite di 20 MB;
  - download solo da utenti con login, sempre come `attachment`;
  - in Docker stanno in un volume dedicato `allegati`, da includere nel backup.
- **Email:**
  - un solo punto di invio (`app/comunicazioni.py`) che registra ogni tentativo in `notifica_email`;
  - dalla pratica l'email va al referente principale, con "Rispondi a" dell'utente che invia;
  - email di prova disponibile nelle impostazioni.
- **Promemoria:** alle 07:30 ogni utente riceve le sue operazioni scadute o in scadenza entro 3 giorni. Al massimo uno ogni 20 ore; niente invio se l'email non è attiva.
- **Segnalatori:** la provvigione è una stima (contributo concesso × percentuale), non un dato contabile.
- **Prefattibilità:**
  - voci automatiche dai requisiti noti del bando: territorio, dimensione, ATECO, stato dell'impresa e del bando, de minimis;
  - ciò che non è noto resta "da verificare";
  - l'esito lo decide il consulente; la pratica aperta dalla verifica resta collegata.
- **Statistiche:** solo aggregati dal database. Tasso di successo = (ammesse + rendicontazione + chiuse) / (quelle + respinte).

## 2026-11-18 — Appalti pubblici: TED sì, ANAC e MEPA solo come link
**Richiesta del committente:** cercare appalti pubblici per categoria e territorio (comune, provincia, regione, Italia), indicando ANAC (BDNCP), MEPA, Portale Appalti MIT e InfoAppalti.
**Verifica delle fonti (18/11/2026):**
- **ANAC (dati.anticorruzione.it):** 70 dataset aperti (CC BY), ma il portale rifiuta ogni richiesta non proveniente da un browser ("Request Rejected", anche con user agent standard). Non si aggira una protezione del genere.
- **MIT (dati.mit.gov.it):** catalogo non raggiungibile (errore 503).
- **MEPA:** serve l'abilitazione e non c'è un accesso pubblico per programmi.
- **InfoAppalti:** servizio privato in abbonamento, senza accesso pubblico per programmi.
- **TED (Tenders Electronic Daily, UE):** API pubblica senza autenticazione, dichiarata per il riuso. Contiene i bandi italiani sopra soglia e quelli pubblicati volontariamente: il 18/11 erano 1.618 aperti.
**Decisione:**
- Raccolta notturna da TED (03:15): bandi di gara italiani con termine per le offerte non scaduto.
- Le regioni e le province vengono dai codici NUTS ufficiali Eurostat (2021 e 2024). La categoria è la divisione CPV (prime due cifre); il comune è quello della stazione appaltante.
- Le altre fonti sono nella pagina come link, con una spiegazione dei loro limiti.
- Per gli appalti sotto soglia si potrà importare a mano il dataset ANAC scaricato dal browser (da valutare).

## 2026-11-18 — Appalti: scheda completa, categorie SOA, Consip e import ANAC
**Richiesta del committente:** per ogni appalto le informazioni di una scheda gara professionale (categorie SOA prevalente e scorporabili, ente con contatti, CIG, date, procedura, criterio, importo, descrizione, riferimenti di legge, documenti) e un riassunto. Inoltre i dati di ANAC, MIT, MEPA e InfoAppalti.
**Come si ottengono i dati, in modo lecito:**
- **TED:** più campi richiesti all'API (nomi verificati sulla specifica `api-v3.yaml`): contatti dell'ente, tipo di procedura, criterio di aggiudicazione, descrizione, base giuridica, link ai documenti e alla piattaforma di gara. CIG e categorie SOA si leggono dal testo quando ci sono; TED non li ha come campi.
- **Consip (MEPA, SDAPA, convenzioni, accordi quadro):**
  - open data ufficiali `dati.consip.it`, licenza CC BY 4.0, letti seguendo il tracciato record ufficiale;
  - il robots.txt vieta /api/ e chiede 10 secondi tra le richieste: si scarica solo il file annuale da /download/, una volta a notte;
  - le singole RdO del MEPA non sono pubbliche (procedure a invito): Consip pubblica solo dati aggregati.
- **ANAC:**
  - il portale rifiuta le richieste automatiche, ma i dati sono aperti (CC BY-SA 4.0);
  - il consulente scarica ogni mese dal browser i file «CIG» e «categorie-opera» e li carica nell'app (Appalti → Importa da ANAC);
  - le colonne si riconoscono dall'intestazione; le categorie si collegano per CIG anche alle gare TED.
- **MIT:** il catalogo era fuori servizio. Con il d.lgs. 36/2023 la pubblicazione dei bandi passa dalla Banca dati di ANAC, quindi il MIT non è più la fonte principale.
- **InfoAppalti:** tolto su richiesta del committente (18/11/2026): servizio privato in abbonamento, non integrato e non più indicato nell'app.
**Riassunto AI:**
- su richiesta, con il modello scelto dal consulente nelle impostazioni di OpenAI (nessun nome di modello nel codice);
- solo dati pubblici della gara; risultato salvato.

## 2026-11-18 — Ricerca testuale comune (bandi, appalti, clienti, pratiche)
Misurata sui dati reali:
- ogni parola si cerca in qualsiasi ordine: «transizione ecologica imprese» da 0 a 18 bandi;
- accenti ignorati con `translate()`: «attivita» da 3 a 411;
- singolare e plurale: «fotovoltaico» da 1 a 7;
- sinonimi del settore (startup, PMI);
- parole generiche (bando, avviso) ignorate;
- una regione scritta nella ricerca diventa il filtro territoriale.
Codice in `app/utils/ricerca.py`.

## 2026-11-18 — Opportunità per cliente (abbinamento automatico, prima versione della fase 4)
**Decisione:** abbinamento **a regole** ogni mattina (07:00) tra i clienti con avvisi attivi e i bandi e appalti aperti. Ogni opportunità ha un punteggio 0-100 e i motivi: requisiti soddisfatti e cose da verificare. Il punteggio con l'AI (similarità e motivazione) si aggiungerà dopo l'estrazione dei requisiti (fase 3).
- **Profilo del cliente:**
  - attestazione SOA (categoria e classifica);
  - regioni di interesse per gli appalti;
  - categorie CPV di interesse;
  - interruttore "avvisi attivi".
- **Bandi:** si escludono per tipo di soggetto, territorio (anche il comune della sede per i bandi comunali), dimensione, ATECO e settore. Un bando chiaramente di settore (cultura, agricoltura, turismo, pesca, sport) va solo a chi ha un ATECO di quel settore.
- **Appalti:**
  - territorio: sede più regioni di interesse; le gare nazionali vanno sempre bene;
  - SOA prevalente posseduta con classifica sufficiente: importi dell'Allegato II.12 al d.lgs. 36/2023, con l'incremento di un quinto;
  - oppure categoria CPV principale di interesse; se è solo secondaria resta "da verificare".
- **Quantità e stato:**
  - al massimo 30 bandi e 30 appalti per cliente, i più pertinenti;
  - le opportunità già lavorate (interessante, scartata, pratica aperta) non vengono più toccate.
- **Email del mattino** a tutti gli utenti attivi con le sole novità. "Segnala al cliente" invia al referente un'email scritta dal consulente.
- Tabella `opportunita` invece di `match` dello schema: serve anche per gli appalti.
- Provato sui dati reali con due clienti tipo (impresa edile del Lazio, software house di Firenze). Le prove hanno portato a togliere tre falsi positivi: bandi di un altro comune, bandi di settore, appalti con categoria solo secondaria.

## 2026-11-18 — Fase 3: estrazione dei requisiti con l'AI
Implementata secondo il brief (sezioni 8 e 10).
- **Schema:**
  - `app/extraction/schema.py`, versione 1;
  - ogni campo è `{valore, confidenza, citazione, da_verificare}`, con vocabolari chiusi: categorie di spesa (12 valori del brief), forme di agevolazione, dimensioni, regime, modalità, regioni;
  - output strutturato OpenAI (JSON Schema `strict`) con la Responses API, verificata sulla SDK installata.
- **Prompt:** versionato in `app/extraction/prompts/estrazione_v1.md`. Al modello si indica la struttura tipica dei bandi (finalità, beneficiari, spese, agevolazione, procedura, termini). Il formato ufficiale del «bando tipo» del Codice degli incentivi resta da trovare e verificare: è in `da-migliorare.md`.
- **Verifica:**
  - la citazione deve comparire nel testo (confronto normalizzato), altrimenti la confidenza diventa «bassa» e il campo «da verificare»;
  - i valori fuori vocabolario o di tipo sbagliato vengono scartati.
- **Risoluzione** (`app/extraction/risoluzione.py`, funzione pura): correzione manuale > fonte > AI.
  - Un dato della fonte che la fonte stessa segna incerto cede a un valore AI con confidenza alta o media.
  - I valori della fonte si salvano in `bando_requisiti.valori_fonte`, così ogni nuova raccolta ricalcola i requisiti senza perdere AI e correzioni. Per i bandi importati prima si ricostruiscono dai campi con provenienza «fonte».
  - Le **date** trovate dall'AI completano solo i bandi senza date (es. portali regionali); lo stato si ricalcola.
- **Quando si estrae:** solo i bandi aperti o in arrivo il cui hash (testo + versione del prompt + versione dello schema + modello) è cambiato. Al massimo N per notte (impostabile), prima quelli che scadono prima.
- **Costi:**
  - ogni chiamata va in `uso_llm`, con il costo stimato dai prezzi inseriti dal consulente: nessun prezzo nel codice;
  - con un budget impostato servono i prezzi del modello, altrimenti l'estrazione non parte;
  - all'80% del budget un'email ai titolari, al 100% sospensione fino al primo giorno del mese successivo;
  - con chiave non valida o credito esaurito il run si ferma e l'errore compare nella pagina.
- **Revisione:**
  - pagina con i bandi che hanno campi da verificare;
  - per ogni campo: valore, confidenza e citazione, con il testo del bando a fianco e le citazioni evidenziate;
  - «Conferma», «Correggi» e «Annulla» la correzione (`bando_override`).
- **Modelli:** in Impostazioni → Intelligenza artificiale (estrazione, valutazione, embeddings), con il «Modello per i riassunti» dell'integrazione come riserva. Anche i riassunti degli appalti registrano i consumi e rispettano il budget.
- **Fatti dopo:** lettura dei PDF dei bandi e punteggio AI delle opportunità (sezioni seguenti).

## 2026-11-18 — PDF dei bandi letti dall'AI
**Misura sui dati reali:**
- **Dataset nazionale:** solo 30 bandi attivi su 761 hanno un link diretto a un PDF. Gli altri rimandano a pagine di ministeri e regioni, che il brief non vuole far navigare in automatico.
- **Portali regionali** (Toscana e Lazio): gli allegati PDF sono nella pagina del bando. Ora si salvano durante la raccolta (Lazio: una richiesta in più per bando, perché la REST API non li contiene).
**Regole:**
- solo link diretti a un PDF, solo bandi aperti o in arrivo;
- al massimo 3 PDF per bando, scelti per utilità: bando, avviso, disciplinare e decreti prima di contatti, modulistica e rendicontazione;
- robots.txt di ogni sito rispettato: nella prima prova 9 PDF non sono stati scaricati per questo;
- 1 richiesta al secondo per dominio, massimo 25 MB, controllo che sia davvero un PDF;
- il testo si estrae con `pypdf` (licenza BSD); i PDF scansionati vengono segnalati, l'OCR non è previsto;
- sha256, testo e percorso in `documento_bando`;
- un PDF condiviso da più bandi si scarica una volta per esecuzione; quelli falliti si ritentano dopo 7 giorni, al massimo 3 volte.
**All'AI:** il testo della fonte più i brani dei PDF con più parole chiave (beneficiari, spese, agevolazione, regime, termini…), fino a 40.000 caratteri. Quando arriva un PDF nuovo cambia l'hash dell'input e il bando viene rianalizzato.
**PDF bloccati (robots.txt o errori):**
- la scheda del bando mostra il motivo, il link al PDF e il pulsante «Carica il PDF»;
- la pagina «PDF da caricare» e un avviso in home li elencano tutti;
- il consulente apre il link nel browser (il robots.txt vale per i programmi, non per le persone), salva e carica: il testo si legge subito e l'AI lo usa alla notte successiva.

**Prima prova reale:** 25 PDF tentati, 15 letti (tra cui il decreto di 63 pagine del bando Internazionalizzazione 2026 della Toscana), 9 vietati dal robots.txt, 1 non trovato.

## 2026-11-18 — Fase 4: punteggio AI delle opportunità

- **Flusso del mattino (07:00):** abbinamento a regole → valutazione AI → email. Se l'AI fallisce l'email parte lo stesso con i punteggi delle regole.
- **Preselezione:** con un modello di embeddings si confronta il profilo anonimo del cliente con la scheda del bando/gara (similarità coseno) e si valutano solo le più simili sopra la soglia, al massimo K per cliente; senza embeddings si prendono le K con il punteggio delle regole più alto. Gli embeddings sono in tabella `embedding` (pgvector) e si ricalcolano solo se cambia l'hash del testo. Il testo per gli embeddings non contiene i controlli delle regole, che dipendono dal cliente.
- **Valutazione:** output JSON con schema rigido (punteggio 0-100, motivazione 2-3 frasi, requisiti soddisfatti / da verificare / mancanti) e griglia esplicita nel prompt (`app/opportunita/prompts/valutazione_v1.md`). Il punteggio finale è quello AI se c'è, altrimenti quello delle regole (entrambi conservati).
- **Niente ricalcoli:** hash di versione prompt + modello + profilo + scheda; se non cambia non si richiama il modello.
- **Limiti:** valutazioni al giorno, per cliente, budget mensile condiviso con l'estrazione; uso registrato in `uso_llm` (operazioni `embedding` e `scoring`).
- **Email:** le opportunità valutate dall'AI entrano nell'email solo sopra la soglia (60 predefinito); quelle con solo il punteggio delle regole restano come prima.
- **Feedback:** «Utile» (→ interessante), «Non pertinente» e «Già noto» (→ scartata, non riproposta né rivalutata). La pagina Statistiche mostra la precisione per fascia di punteggio, per tarare la soglia.

## 2026-11-18 — Verifica completa contro brief e preventivo

Riletti il brief e il preventivo e implementato quello che mancava:
- **CRM completo:** progetti d'investimento con categorie di spesa (vocabolario chiuso come
  colonna ARRAY invece di una tabella ponte: stessi valori dell'estrazione AI, più semplice),
  attività, scadenze dei clienti, aiuti de minimis con plafond residuo su 3 anni mobili
  (massimali configurabili), dimensione stimata (Racc. 2003/361/CE, la manuale prevale),
  tag, consensi dei referenti, filtri della lista clienti.
- **Filtri del matching:** anzianità, forma giuridica, plafond de minimis, dimensione stimata
  (solo «da verificare», perché senza ULA è una stima), spese dei progetti. Ogni esclusione
  ha un motivo; si esclude solo con requisito certo e dato noto. Le esclusioni non si salvano
  in tabella: il comando `collaudo` le ricalcola con il motivo per tutte le coppie.
- **Embeddings dei progetti:** i progetti entrano (anonimi) nel profilo del cliente usato per
  embeddings e punteggio, invece di un vettore per progetto: un solo confronto per coppia.
- **Import CSV** dal CRM precedente ed **export completo** in zip; il collaboratore non esporta.
- **Aggiornamenti ai clienti** (email e WhatsApp), con approvazione, consensi e disiscrizione.
- **Email del mattino** completa e sempre inviata; **dashboard** con spesa AI e bozze.
- **Report PDF** con WeasyPrint (librerie nell'immagine Docker; su Windows non disponibile).
- **Collaudo:** `collaudo-prepara` e `collaudo`, set proposto in `docs/collaudo/`. Ha già
  trovato un'esclusione sbagliata («sport» dentro «esportatrici»), corretta.
- **Backup con restic** (come da brief), al posto dei file GPG della prima versione.
- **Duplicati** tra fonte nazionale e regionale collegati con `bando_canonico_id`.
- **Antivirus** ClamAV sui file caricati, **rotazione della SECRET_KEY**, avviso di scadenza
  della sessione. Tolto InfoAppalti su richiesta del committente.
- **Non fatti, con motivo:** tabella ISTAT dei comuni e raccordo ATECO 2007/2025 (file ISTAT
  da scaricare e verificare: i confronti oggi sono per nome e per prefisso ATECO); OCR dei
  PDF scansionati; formato del «bando tipo» (da trovare la fonte ufficiale).

## 2026-11-18 — Interfaccia: stile proprio al posto di Pico.css
Tolto Pico.css: `app/static/css/app.css` è l'unico foglio di stile e disegna da solo moduli, tabelle, pulsanti e pannelli. Navigazione in una barra laterale raggruppata (Lavoro, Bandi e gare, Studio, Amministrazione) al posto del menu in alto con la tendina. Carattere unico **Schibsted Grotesk** (licenza OFL), salvato in `app/static/fonts` insieme alla licenza: nessun font da CDN.
**Motivo:** Pico dava un aspetto generico e difficile da personalizzare. Lo studio usa l'app tutto il giorno, quindi le superfici sono neutre e il verde del marchio compare solo sull'azione principale, sulla voce di menu attiva e sulle cifre chiave. Tema sempre chiaro, anche se il computer è in modalità scura (scelta del cliente); i colori scuri restano nel CSS, attivabili con `data-tema="scuro"`.
**Regole:** griglia di 4px; pesi 400 e 500 (600 di rado); cifre tabellari per date e importi; niente maiuscolo spaziato, gradienti, ombre colorate o emoji come icone.

## 2026-11-18 — ISTAT, OCR, bando-tipo e prova della piattaforma come lo studio

- **Tabelle ISTAT** (fonti ufficiali verificate): elenco dei comuni dal link permanente
  ISTAT e tavola di raccordo ATECO 2025 ↔ 2007 agg. 2022 (versione del 15/07/2026).
  Caricate con `carica-istat` e ogni mese dal worker; colonne cercate per nome. Usi: codice
  ISTAT della sede, comuni dei bandi confrontati per codice, province per sigla, ATECO di
  versioni diverse convertiti (si esclude solo senza alcuna corrispondenza).
- **OCR** con PyMuPDF + Tesseract (italiano), solo per i PDF senza testo; testo marcato come
  OCR per l'AI. Tesseract è nell'immagine Docker e in CI (test con un PDF di sola immagine).
- **Bando-tipo** (decreto MIMIT 18/06/2026, GURI n. 164 del 17/07/2026, Allegato I): le 15
  partizioni nel prompt v2 e, nei PDF lunghi conformi, prima le partizioni con i requisiti.
  Il cambio di versione del prompt fa ristrutturare i bandi aperti, gradualmente (limite per
  notte e budget).
- **Prova da utente** (cliente reale inserito dal form, opportunità, pratica, report,
  aggiornamento) e **verifica sui siti ufficiali**. Problemi trovati e corretti:
  - proposte non pertinenti (famiglie, scuole, presepi, professionisti): ora contano i
    settori della fonte, i tipi di beneficiario e le parole del titolo;
  - CCIAA Frosinone-Latina registrata dalla fonte come «Lazio» ma valida solo per FR e LT
    (verificato sul sito della Camera): province ricavate dal nome dell'ente;
  - «Nuovo Fondo Futuro» primo per un'impresa di 17 anni, ma il testo è per microimprese in
    avviamento: segnali nella descrizione;
  - esclusione sbagliata del Conto Termico 3.0 (settore «Edilizia» = tipo di intervento):
    con 1-2 settori non si esclude;
  - aggiornamento al cliente che diceva «6141 nuovi bandi» (l'archivio iniziale): ora solo i
    bandi davvero nuovi;
  - ricerca «digitalizzazione» che non trovava «digitale»: sinonimi del settore;
  - etichette tecniche nel report PDF, doppio messaggio di errore, testi al plurale.
- **Nota sulla pubblicazione su Vercel** (fatta da un collaboratore): Vercel esegue l'app
  web ma non il worker (raccolte notturne, email del mattino), non conserva i file caricati
  e non ha ClamAV, WeasyPrint e Tesseract. Per l'uso reale serve l'installazione Docker
  (`docs/messa-in-produzione.md`) o un servizio esterno per job e file.

## 2026-11-18 — Utenti e permessi; PDF vietati dai siti

- **Utenti e permessi** (Impostazioni → Utenti e permessi, solo titolare): ruoli titolare,
  collaboratore e sola lettura; per collaboratori e sola lettura si scelgono le sezioni
  (clienti, opportunità, pratiche, bandi, appalti, aggiornamenti, prefattibilità,
  segnalatori, statistiche, importazione, esportazione). Il controllo è su ogni richiesta in
  base all'indirizzo (app/auth/permessi.py), menu e home mostrano solo ciò che si può usare.
  Password temporanea generata e mostrata una sola volta al titolare; 2FA obbligatoria al
  primo accesso. Sempre almeno un titolare attivo; nessuno disattiva sé stesso.
  Sostituisce la proposta del brief «il collaboratore non esporta»: ora lo decide il titolare
  (predefinito: niente import ed export).
- **PDF vietati** (es. Parco Gran Sasso-Laga: robots.txt «User-agent: * Disallow: /»): non si
  aggira il divieto, nemmeno facendo scaricare il file all'AI. Il consulente li scarica dal
  browser e li carica tutti insieme: abbinamento automatico per nome del file. L'AI riceve
  comunque il link dei documenti non letti e segna «da verificare» ciò che potrebbero dire.

## 2026-11-18 — Documentazione delle gare

Per ogni gara la scheda elenca la documentazione per tipo (Bando GUUE, bando, disciplinare,
capitolato, domanda di partecipazione, DGUE, offerta, modulistica, schema di contratto,
progetto, atti, chiarimenti, altri allegati); ogni voce è un link che scarica il file dal
sito dell'ente. Fonti verificate sulle gare aperte del 18/11/2026:
- PDF dell'avviso su TED (ted.europa.eu/it/notice/{numero}/pdf), consentito da robots.txt;
- pagina della gara sulla piattaforma indicata da TED, letta ogni notte (al massimo 150 gare,
  ogni gara rivista dopo 7 giorni) o con «Cerca i documenti adesso»: funziona su Asmecomm,
  Soresa, SardegnaCAT, Giada (10-18 documenti per gara); le piattaforme solo JavaScript
  (START Toscana), i link alla sola home (Sintel, MEPA) e i siti che lo vietano nel
  robots.txt (portali «PortaleAppalti» di MIT e RAI) restano con il link alla pagina.
I file non si scaricano sul server: si leggono solo le pagine, i documenti li apre chi clicca.

## 2026-11-18 — Pubblicazione su Vercel (installazione NuvioLab)
L'istanza NuvioLab gira su Vercel (`europrojecta-studio.vercel.app`, funzioni a Francoforte) con database Neon in UE, per scelta del titolare. `.vercelignore` esclude segreti e dati locali; Vercel installa le dipendenze da `uv.lock`.
**Job notturni:** il worker non gira; al suo posto Vercel Cron chiama `/cron/<job>` (`app/jobs/cron.py`, protetto da `CRON_SECRET`) agli orari di `vercel.json`, in UTC e distanziati di un'ora perché sul piano Hobby l'orario è preciso solo all'ora. Ogni job ha al massimo 300 secondi (limite del piano Hobby): la raccolta dei bandi ne ha richiesti 68, quella degli appalti 232.
**Documenti e backup su Vercel:** `ARCHIVIO_ALLEGATI=database` salva i file caricati nella tabella `allegato_contenuto`; ogni notte `/cron/backup` (`app/backup_esterno.py`) carica su uno storage S3 esterno l'esportazione completa cifrata con `BACKUP_PASSPHRASE` (scrypt + AES-256-GCM a blocchi), tenendo `BACKUP_GIORNI` giorni. Si apre con `python -m app.cli backup-decifra`.
**Su Vercel al posto di ciò che manca:** report PDF con PyMuPDF quando WeasyPrint non ha le sue librerie; PDF scansionati trascritti dall'AI dello studio quando manca Tesseract (al massimo 3 per notte, costo in `uso_llm`). **Limiti che restano:** niente antivirus ClamAV sui file caricati; ogni job ha al massimo 300 secondi (piano Hobby). La guida `docs/messa-in-produzione.md` (VPS + Docker Compose) resta il modo completo.
Le migrazioni girano da sole a ogni pubblicazione (`buildCommand` in `vercel.json`, con `DATABASE_URL_UNPOOLED` di Neon): le credenziali del database restano su Vercel. Il primo titolare si crea dal browser con la pagina `/primo-accesso` (`PRIMO_ACCESSO=true`), che sparisce appena esiste un utente.

## 2026-11-18 — Verifica in due passaggi disattivabile
Nuova impostazione `VERIFICA_DUE_PASSAGGI` (predefinita `true`). Con `false` il login con la password apre subito la sessione completa. Spenta solo sull'istanza Vercel di NuvioLab, su richiesta del titolare; le altre installazioni restano con la 2FA obbligatoria.
**Rischio accettato:** l'app è raggiungibile da Internet e contiene dati dei clienti; senza 2FA la protezione è solo la password (con blocco dopo 5 tentativi).

## 2026-11-19 — Primo accesso, monitoraggio dei job, revisione del codice

- **Primo accesso**: chi entra con una password temporanea deve cambiarla prima di usare l'app
  (tutte le pagine rimandano a «Il mio account»). In alternativa il titolare manda un invito
  via email: link monouso valido 72 ore, nel database solo l'impronta HMAC del token; se
  l'email non parte si torna alla password temporanea. Nessuna password viaggia per email.
- **Job notturni**: il worker registra un battito ogni 15 minuti. Problemi segnalati: worker
  silenzioso da oltre 45 minuti, job non partito oltre il previsto (26 ore per i giornalieri,
  33 giorni per le tabelle ISTAT), job «in corso» da oltre 3 ore, ultima esecuzione fallita.
  Si vedono nella home del titolare e nell'email del mattino; email immediata ai titolari al
  massimo una volta al giorno per problema.
- **Revisione indipendente del codice** (11 punti, tutti corretti):
  - permessi: «/allegati» appartiene alle pratiche, aggiornamenti e report dei clienti alla
    sezione aggiornamenti; gli indirizzi non mappati sono negati ai non titolari;
  - SSRF: ogni richiesta del server (anche dopo un reindirizzamento) va solo a indirizzi
    pubblici in http/https sulle porte 80/443; gli URL arrivano anche da TED e dalle pagine
    delle piattaforme;
  - rilettura settimanale delle gare: solo la pagina della gara, mai i file elencati, letta in
    streaming con limite di 5 MB e solo se è HTML; un link malformato non ferma la notte;
    tenuti solo link http/https;
  - territorio: l'esclusione per altre regioni viene prima del controllo sugli enti locali,
    che vale solo per Camere di commercio, Province, Comuni e Città metropolitane (non per
    «Regione Friuli Venezia Giulia» o «Regione Molise»);
  - PDF caricati insieme: niente abbinamento automatico per nomi usati da link diversi
    (es. «bando.pdf») né per più file con lo stesso nome nello stesso invio; ogni file si
    legge una volta sola;
  - OCR: risoluzione ridotta per le pagine molto grandi (tavole), per non esaurire la memoria;
  - aggiornamenti ai clienti: all'AI va solo la parte sui bandi; pratiche e scadenze (nomi
    scritti dallo studio) restano sul server;
  - «almeno un titolare attivo»: controllo con blocco delle righe, così due titolari che si
    declassano a vicenda nello stesso momento non lasciano lo studio senza titolare.
- **START Toscana** (piattaforma Pleiade): esiste un'API pubblica delle gare, ma il robots.txt
  consente le visite solo dalle 23 alle 4 e vieta i percorsi degli allegati. Non si è usata
  senza poterne verificare il formato nella fascia consentita: resta il link alla pagina e il
  PDF di TED. Da verificare di notte prima di implementarla.

## 2026-11-19 — Uso quotidiano: Oggi, ricerca, calendario, app, note, Segui

- **Oggi** è la pagina personale; la Home resta il quadro dello studio.
- **Calendario .ics**: link personale con token (nel database solo l'impronta HMAC, come per
  inviti e sessioni), rigenerabile e disattivabile; il file contiene nomi dei clienti e delle
  pratiche, perché serve a chi lavora. Non c'è un'API di Google o Microsoft: è il calendario
  dell'utente che scarica il file (vedi `docs/dati-in-uscita.md`).
- **Ricerca unica**: una query per gruppo (clienti, pratiche, bandi, gare), ognuno mostrato solo
  a chi ha la sezione; P.IVA, codice fiscale, codice pratica e CIG cercati anche esatti.
- **App sul telefono**: manifest web (installazione dalla schermata Home) e service worker
  (/sw.js) che tiene in memoria solo i file statici e mostra «Sei offline» senza rete: le
  pagine con i dati non si salvano mai sul telefono (Cache-Control: no-store). Sotto i 640px
  le tabelle degli elenchi diventano schede, sotto i 700px i filtri stanno dietro «Filtri»,
  sotto i 900px c'è la barra in basso (Oggi, Clienti, Opportunità, Scadenze, Cerca).
  Opportunità a pagine da 100 (prima fino a 500 in una pagina da 1 MB). CSS e JS con ?v= si
  tengono un anno. Ricerca su bandi e appalti con indici a trigrammi (migrazione 0025).
- **Note**: menzioni con @nome di battesimo o @parte dell'email; ognuno elimina le proprie, il
  titolare tutte. Chi è in sola lettura legge le note ma non scrive.
- **Segui**: istantanea di scadenza, apertura, stato, documenti (link) e impronta del testo,
  confrontata ogni mattina nel job delle opportunità, prima dell'email; anche chi è in sola
  lettura può seguire.

## 2026-11-19 — Verifica generale: permessi delle note, PDF dalle pagine ufficiali

- **Note e permessi**: si citano e si avvisano solo i colleghi che vedono la sezione (pratiche o
  opportunità); «Oggi», contatore, email del mattino e calendario mostrano solo ciò che le sezioni
  attuali dell'utente permettono. Le note di pratiche, clienti e opportunità eliminate spariscono
  con le menzioni; un'opportunità con note non viene più tolta in automatico.
- **PDF dalla pagina ufficiale**: circa il 95% dei bandi attivi non aveva PDF perché Incentivi.gov.it
  dà solo il link alla pagina dell'ente. Il passo notturno dei PDF ora legge fino a 30 di queste
  pagine per notte (robots.txt e pause tra richieste allo stesso sito rispettati; si legge la
  pagina, i file li scarica il passo dei PDF come sempre), senza intestazione, menu e piè di
  pagina, e registra al massimo 6 PDF utili per bando (bando, avviso, decreti, allegati, FAQ;
  esclusi note legali, cookie, privacy, accessibilità). Ogni pagina si rilegge dopo 14 giorni
  finché non si trovano PDF. Pagine che caricano i file con JavaScript: resta il link alla pagina.
- **Codifica delle pagine**: intestazione HTTP, poi `<meta charset>`, poi UTF-8, altrimenti
  Windows-1252 (prima comparivano «�» al posto di «–» e lettere accentate, anche nelle gare).
- **PDF con caratteri nulli**: il testo viene ripulito; un errore imprevisto su un PDF non ferma
  più gli altri della notte (prima lo stesso PDF avrebbe bloccato il passo ogni notte).

## 2026-11-19 — Sedi operative nell'abbinamento

- Prima contava solo la sede legale: le «Regioni di interesse» valevano solo per le gare e il
  modulo cliente non permetteva di inserire sedi operative (il modello le prevedeva già).
- Ora il modulo ha il campo «Sedi operative» (un comune per riga, provincia tra parentesi se il
  nome è ambiguo), riconosciuto con la tabella ISTAT (provincia e regione ricavate); comuni
  sconosciuti danno errore. Le sedi già presenti tengono indirizzo e CAP.
- Abbinamento dei bandi: regioni, province, comuni ed enti locali (CCIAA, Province, Comuni) si
  confrontano con **tutte** le sedi; si esclude solo se tutte le sedi sono note e nessuna è nel
  territorio. Il motivo dice se vale per la sede legale o per quella operativa. Gare: regioni
  delle sedi più le regioni di interesse. La prefattibilità e il profilo per l'AI (solo regione
  e provincia) citano le sedi operative.
- Verifica sui dati reali: con una sede a Prato il cliente di Viterbo riceve 20 bandi toscani,
  ma il tetto di 30 opportunità per tipo ne toglieva 13 del Lazio. Ora il tetto cresce di 15 per
  ogni regione in più delle sedi (al massimo 60). Il campo accetta anche «Prato PO»,
  «Prato, PO» e il CAP davanti; con la provincia sbagliata l'errore indica quella giusta.

## 2026-11-19 — PDF dei bandi in Word

- Richiesta: documenti dei bandi modificabili con Word o Google Documenti. Pulsante «Word»
  accanto a ogni PDF che l'app ha salvato o letto (`/bandi/{id}/documenti/{doc}/word`).
- Conversione in Python, senza servizi esterni (va anche su Vercel): PyMuPDF (già usato per
  l'OCR) legge testo, corpi e grassetti; `python-docx` (nuova dipendenza, MIT) scrive il file.
  Righe spezzate riunite, parole col trattino ricomposte, voci di elenco separate, titoli dal
  corpo del carattere o dal grassetto, intestazioni e piè di pagina ripetuti e numeri di pagina
  tolti, tabelle con le linee ricostruite come tabelle Word. Scansioni o file mancanti: si usa il
  testo già letto (OCR compreso).
- Tabelle: `find_tables(use_layout=False)`. Con il modello di layout (predefinito in PyMuPDF 2)
  la ricerca costava 1–4 s a pagina e su un PDF reale mandava in crash il processo. Le tabelle si
  cercano solo nelle pagine con almeno 6 linee disegnate, al massimo 8 s per documento.
- Il file Word si scrive direttamente nell'XML: con le funzioni normali di python-docx un PDF di
  314 pagine richiedeva oltre 100 s (ogni paragrafo nuovo scorre tutti i precedenti).
- Google Documenti: niente collegamento a Google Drive (servirebbe l'accesso OAuth all'account);
  il .docx si carica su Drive e si apre con Google Documenti.
- Anche il report per il cliente si scarica in Word (dopo l'approvazione, come il PDF), scritto
  direttamente dalle sezioni del report. Il Word di un PDF lungo si salva accanto al PDF
  (`<nome>.word1.docx`): dalla seconda volta si scarica subito.
- Verifica: tutti i 127 PDF salvati convertiti senza errori (mediana 0,9 s, massimo 21 s per
  314 pagine); 5 simulazioni d'uso complete su una copia del database senza errori.

## Decisioni dal brief (riassunto)
- FastAPI con endpoint sincroni; PostgreSQL 16 + pgvector; SQLAlchemy 2 (`Mapped[...]`), psycopg 3, Alembic.
- Jinja2 + HTMX salvati nel repository, nessuna risorsa da CDN (Pico.css sostituito da uno stile proprio, vedi sopra).
- Worker APScheduler con advisory lock di Postgres.
- LLM e embeddings OpenAI sull'account del cliente, dietro interfacce sostituibili; nessun nome di modello nel codice.
- Autenticazione propria: argon2id + TOTP + sessioni lato server.
- Deploy Docker Compose su VPS con Caddy; backup restic cifrati.
- Enum come varchar con check constraint; importi `Numeric(14,2)`; nomi di dominio in italiano.

## Cestino con copia, non «eliminazione logica»
Quello che si elimina va nella tabella `cestino` come copia JSON: la riga e tutte quelle che il
database elimina con lei (chiavi esterne ON DELETE CASCADE seguite a catena, più le note di
pratiche e opportunità che non hanno chiave esterna), con i collegamenti che l'eliminazione
azzera (SET NULL). Il ripristino reinserisce le righe con gli stessi id nell'ordine delle
dipendenze. Alternativa scartata: una colonna `eliminato_il` in ogni tabella, che obbliga a
filtrare ogni query (un filtro dimenticato mostra dati eliminati). I file degli allegati e dei
PDF dei bandi restano sul disco finché l'elemento non lascia il cestino (30 giorni, job
`pulizia`). Se nel frattempo è nato un conflitto (stessa partita IVA, genitore eliminato) il
ripristino si ferma e spiega il motivo, senza modificare nulla.

## Le esigenze del cliente pesano più dei requisiti noti
Prova del 21/11/2026 con quattro clienti tipo: il punteggio premiava i bandi con molti requisiti
compilati (regione, dimensione, ATECO, settori: fino a +55) e i progetti del cliente contavano
solo se il bando aveva le spese ammissibili, cioè quasi mai (0 bandi su 825: arrivano
dall'estrazione AI dei PDF). Un'officina che voleva comprare macchinari riceveva la Nuova
Sabatini al 76° posto, fuori dalle 30 opportunità tenute. Ora, se il cliente ha progetti attivi,
il bando sale di 30 punti se li finanzia (spese ammissibili, oppure parole del titolo e della
presentazione), di 5 se lo dice solo la finalità della fonte (generica: «Sostegno
investimenti» è su un bando su tre), e scende di 20 se non c'entra. Non si esclude nulla: le
fonti sono incomplete. Le misure di Invitalia prendono i requisiti mancanti dal doppione di
Incentivi Italia (`completa_requisiti`), altrimenti sembravano bandi senza dati.

## L'AI legge anche la pagina ufficiale del bando, non solo i PDF
Molti PDF non si scaricano (robots.txt, siti lenti, link rotti) e molte fonti danno solo il link
alla pagina dell'ente. Ogni notte (120 pagine, prima i bandi senza nessun testo) si legge la
pagina ufficiale e se ne salva il testo (`bando.testo_pagina`, senza menu e piè di pagina):
l'AI lo riceve insieme ai PDF, e da solo quando i PDF mancano. Un link «documento» che porta a
una pagina web invece che a un PDF viene letto come pagina (mime `text/html`, nessun file
salvato). Robots.txt e le pause tra richieste allo stesso sito restano rispettati. Il controllo
«pagina precisa e non home» delle gare chiedeva due livelli di indirizzo e scartava metà delle
pagine dei bandi («…camcom.it/bando-voucher-turismo-2026»): per i bandi ne basta uno.

## Collaudo dell'abbinamento su 50 aziende (21/11/2026)
50 aziende di 16 tipi (officina, startup, hotel, azienda agricola, edile, ristorante,
commercio, export alimentare, studio tecnico, laboratorio R&S, trasporti, parrucchiere,
cooperativa sociale, mobilificio, cantina, agenzia web) in tutte le regioni, una su cinque con
una sede operativa altrove, contro gli 825 bandi aperti. Controlli automatici sulle prime 30:
altra regione o provincia, dimensione, settore, bandi non per imprese, scaduti, doppioni,
misure attese assenti (Sabatini, Smart&Start, Conto Termico, Parco Agrisolare…).
Corretti: Camere di commercio di altre province sopra la soglia grazie ai bonus (ora un tetto
sotto la soglia), Province autonome di Trento e Bolzano confuse, comuni senza territorio
indicato, «impianti» letto come macchinari, bonus pieno per ATECO ammesso anche quando il bando
ammette quasi tutti i settori, punteggi sopra 100 tagliati e quindi alla pari, la stessa
misura in due schede della stessa fonte, bandi con data di chiusura passata se la raccolta
notturna non ha ancora aggiornato lo stato.

## L'analisi AI resta notturna, ma solo per cambiamenti veri
L'analisi gira ogni notte ma tocca solo i bandi nuovi o cambiati (hash dell'input): farla una
volta a settimana non farebbe risparmiare, ritarderebbe solo i bandi nuovi (dannoso per
sportelli e click day). Lo spreco era un altro: la pagina ufficiale riletta ogni 14 giorni
cambia quasi sempre in qualcosa di inutile (voti, «Condividi», date di aggiornamento, codici
casuali della Regione Marche) e ogni testo diverso rimandava il bando all'AI. Ora
(`cambiamento_sostanziale`) il testo nuovo sostituisce il vecchio solo se cambiano proroghe,
sospensioni, date, importi o requisiti, o più del 10% del testo; le pagine dei bandi che
hanno già PDF letti si rileggono ogni 30 giorni. Misura su 22 pagine vere rilette (22/11/2026):
12 identiche, 10 diverse, nessuna con un cambiamento sostanziale.

## Appalti: pubblicità legale ANAC automatica e collaudo (22/11/2026)
Controllo della parte appalti: sul sito c'erano 2.072 gare (TED e Consip) e nessuna ANAC, quindi
nessun affidamento sotto soglia e il filtro SOA sempre a 0. Dal 2024 tutti i bandi passano dalla
Piattaforma di Pubblicità a Valore Legale di ANAC, pubblica e leggibile da un programma: nuova
fonte notturna (`app/appalti/pvl.py`, docs/fonti/anac-pubblicita-legale.md). Prova su 12 giorni:
349 gare sotto soglia e 432 gare TED completate con CIG e categorie SOA.
Corretti anche: 144 gare TED su 1.618 senza luogo («ovunque») trattate come nazionali anche se
di province, comuni e ASL (ora il luogo si ricava dal comune, salvo enti nazionali ed europei);
85 titoli TED con entità HTML; interessi CPV solo per divisione, troppo ampi (ora anche codici
precisi, che sostituiscono la divisione); ricerca senza sinonimi del settore («scuola» non
trovava «scolastico»); «oggi» calcolato in UTC nei container (TZ=Europe/Rome nell'immagine).

## 2026-11-23 — «Password dimenticata»
Dal login si chiede un link via email (SMTP di Impostazioni → Integrazioni) valido un'ora e usabile una volta; nel database solo l'impronta del token. La risposta è identica per email registrate e no. Limiti: un link ogni 2 minuti per persona, 5 richieste ogni 15 minuti per IP (contate a parte, non come accessi falliti). Dopo il cambio si chiudono tutte le sessioni e si azzera il blocco; la 2FA resta. Senza email configurata: `python -m app.cli reimposta-password` dal server.

## 2026-11-23 — Clienti enti pubblici
Nuovo campo `azienda.tipo_soggetto` (`impresa` | `ente_pubblico`) e `abitanti` (migrazione 0027; i clienti «Comune di…», «Provincia di…» esistenti diventano enti). Per un ente l'abbinamento (`_beneficiari_ente`) passa solo i bandi con enti pubblici tra i beneficiari; senza beneficiari indicati serve una parola da enti nel titolo, altrimenti il punteggio va sotto soglia. Salta ATECO, dimensione, forma giuridica, anzianità, de minimis e settore; nessuna gara d'appalto. Il limite di abitanti esclude solo se è nel titolo: nel testo del bando «parcheggi urbani nei Comuni del Lazio» i 5.000 abitanti decidono solo la percentuale (prova sui comuni di Viterbo). All'AI va il profilo dell'ente con la fascia di popolazione, non il numero esatto. Dopo la prova su Sondrio, Pedavena e la Provincia di Viterbo: restano sotto soglia i bandi per Aler, sanità, università e Camere di commercio, quelli con «imprese» nel titolo e quelli pubblicati dallo stesso ente. Prompt di valutazione v2 con le regole per gli enti; nel report «Perché è adatto al vostro ente». Seconda prova su 12 enti in 9 regioni: il titolo con «professionisti» o «imprese» abbassa sotto soglia anche quando la fonte indica solo «Ente Pubblico» (FVG, aggiornamento dei professionisti); «organismi» tra gli altri enti; bandi aperti a 8 o più tipi di soggetti -15. Filtro «Tipo» nell'elenco clienti. Corretti anche SOLO_MICRO e ALTRE_DIMENSIONI, che contenevano un carattere di controllo al posto di `\b` e non scattavano mai.

