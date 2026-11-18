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

---

## Decisioni dal brief (riassunto)
- FastAPI con endpoint sincroni; PostgreSQL 16 + pgvector; SQLAlchemy 2 (`Mapped[...]`), psycopg 3, Alembic.
- Jinja2 + HTMX + Pico.css salvati nel repository, nessuna risorsa da CDN.
- Worker APScheduler con advisory lock di Postgres.
- LLM e embeddings OpenAI sull'account del cliente, dietro interfacce sostituibili; nessun nome di modello nel codice.
- Autenticazione propria: argon2id + TOTP + sessioni lato server.
- Deploy Docker Compose su VPS con Caddy; backup restic cifrati.
- Enum come varchar con check constraint; importi `Numeric(14,2)`; nomi di dominio in italiano.
