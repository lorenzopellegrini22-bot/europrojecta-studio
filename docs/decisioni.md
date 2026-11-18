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

---

## Decisioni dal brief (riassunto)
- FastAPI con endpoint sincroni; PostgreSQL 16 + pgvector; SQLAlchemy 2 (`Mapped[...]`), psycopg 3, Alembic.
- Jinja2 + HTMX + Pico.css salvati nel repository, nessuna risorsa da CDN.
- Worker APScheduler con advisory lock di Postgres.
- LLM e embeddings OpenAI sull'account del cliente, dietro interfacce sostituibili; nessun nome di modello nel codice.
- Autenticazione propria: argon2id + TOTP + sessioni lato server.
- Deploy Docker Compose su VPS con Caddy; backup restic cifrati.
- Enum come varchar con check constraint; importi `Numeric(14,2)`; nomi di dominio in italiano.
