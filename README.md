# Europrojecta Studio

Gestionale clienti e bandi per la consulenza di finanza agevolata. Software di NuvioLab, concesso in licenza d'uso: un'istanza per cliente.

Ha tre parti:
1. **CRM verticale**: aziende clienti, referenti, progetti d'investimento, pratiche, attività e scadenze.
2. **Motore di matching**: ogni notte raccoglie i bandi nazionali e regionali, li struttura con l'LLM e segnala per quali aziende sono compatibili, spiegando il perché.
3. **Comunicazione**: email del mattino al consulente e aggiornamenti ai suoi clienti, ognuno approvato da lui.

> **Stato attuale (18/11/2026):** tutto il perimetro del brief e del preventivo è implementato.
> - **CRM:** clienti con sedi, ATECO, referenti con consensi, progetti d'investimento, attività,
>   scadenze, de minimis con plafond, tag, dimensione stimata; **import da CSV** del CRM
>   precedente; **export completo** in zip; compilazione dal **Registro Imprese** (Openapi).
> - **Bandi:** Incentivi Italia + fonti regionali di Toscana e Lazio ogni notte, doppioni
>   collegati, PDF letti dall'AI (o caricati a mano se il sito non li lascia scaricare).
> - **AI sull'account OpenAI del cliente:** estrazione dei requisiti con citazioni verificate e
>   revisione, punteggio delle opportunità con embeddings e griglia, budget e consumi.
> - **Matching:** filtri a regole con motivo per ogni esclusione, punteggio AI, feedback e
>   precisione per fascia; comando **collaudo** (20 bandi reali, 10 aziende).
> - **Comunicazione:** email del mattino completa, **aggiornamenti ai clienti** via email o
>   WhatsApp con approvazione e disiscrizione, **report PDF** del bando per il cliente.
> - **Pratiche a fasi** da modelli, scadenzario, prefattibilità, segnalatori, statistiche.
> - **Appalti pubblici:** TED, Consip, import ANAC, scheda gara con categorie SOA.
> - **Produzione:** Caddy (https), ClamAV, backup restic con prova di ripristino, guida in
>   `docs/messa-in-produzione.md`; manuale d'uso in `docs/manuale.md`.
>
> Le scelte sono in `docs/decisioni.md`, i prossimi passi in `docs/da-migliorare.md`.

## Raccolta dei bandi

Il **worker** (`python -m app.jobs.worker`, servizio `worker` in Docker Compose) raccoglie i bandi **ogni notte alle 02:30**. L'orario si cambia con `RACCOLTA_ORARIO=HH:MM`.

Come lavora:
- scarica il file solo se la versione è cambiata;
- riprende i download troncati;
- salva lo storico delle modifiche;
- chiude i bandi scaduti o spariti dalla fonte;
- si ferma senza toccare nulla se il file sembra incompleto.

Se il server era spento all'ora prevista, la raccolta parte appena il worker riparte (entro 6 ore). Ogni esecuzione è visibile nella pagina **Fonti** e nella tabella `job_run`.

Per lanciarla subito: il pulsante "Esegui la raccolta adesso" in **Fonti**, oppure:

```bash
uv run python -m app.cli raccogli-bandi            # come di notte
uv run python -m app.cli raccogli-bandi --forza    # riscarica e ricalcola tutti i requisiti
```

## Primo accesso

```bash
uv run python -m app.cli crea-utente --email nome@studio.it --nome "Nome Cognome"
```

Al primo login l'app chiede di configurare la verifica in due passaggi (QR code da inquadrare con un'app di autenticazione) e mostra 10 codici di recupero. Chi perde telefono e codici: `uv run python -m app.cli reimposta-2fa --email ...`.

Le chiavi API si collegano da **Impostazioni → Integrazioni** (solo il titolare):
- si salvano cifrate e si vede solo l'anteprima delle ultime 4 cifre;
- il pulsante "Verifica connessione" controlla che funzionino;
- in alternativa si possono dare come variabili d'ambiente (vedi `.env.example`); il valore salvato dall'app prevale.

## Stack

Python 3.12, FastAPI (endpoint sincroni), PostgreSQL 16 + pgvector, SQLAlchemy 2 + Alembic, Jinja2 + HTMX (stile proprio, nessun framework CSS), APScheduler nel worker, uv / ruff / pytest, Docker Compose.

## Avvio in sviluppo

Prerequisiti: [uv](https://docs.astral.sh/uv/) e Docker.

```bash
cp .env.example .env          # poi compila POSTGRES_PASSWORD e SECRET_KEY
uv sync                       # crea .venv con Python 3.12 e le dipendenze
docker compose up -d --build  # db + app (migrazioni automatiche) + worker
curl http://localhost:8000/health
```

Senza Docker, con un PostgreSQL + pgvector installato via pip (`pgserver`, utile su Windows):

```bash
uv run --no-project --with pgserver python -c "import pgserver; print(pgserver.get_server('dati/pg', cleanup_mode=None).get_uri())"
# poi DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:<porta>/europrojecta
```

Solo il db in container:

```bash
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run python -m app.jobs.worker
```

## Comandi utili

| Comando | A cosa serve |
|---|---|
| `uv run pytest` | Tutti i test. Senza `TEST_DATABASE_URL` i test di integrazione vengono saltati |
| `uv run pytest -m "not integration"` | Solo i test unitari |
| `uv run ruff check . && uv run ruff format .` | Lint e formattazione |
| `uv run alembic revision -m "descrizione"` | Nuova migrazione (scritta a mano) |
| `uv run alembic upgrade head` | Applica le migrazioni |
| `uv run python -m app.cli crea-utente --email ... --nome ... [--ruolo collaboratore]` | Crea un utente (la password viene chiesta) |
| `uv run python -m app.cli reimposta-2fa --email ...` | Azzera la 2FA di un utente |
| `uv run python -m app.cli raccogli-bandi [--forza]` | Raccolta dei bandi immediata |
| `uv run python -m app.cli pulizia` | Elimina le sessioni scadute e i log vecchi |
| `uv run python -m app.jobs.worker` | Avvia il worker con i job notturni |
| `uv run python -m app.cli opportunita` | Abbinamento, punteggio AI, bozze ed email del mattino subito |
| `uv run python -m app.cli estrai-bandi` / `documenti-bandi` | Estrazione AI / download dei PDF subito |
| `uv run python -m app.cli raccogli-appalti` | Raccolta degli appalti (TED, Consip) subito |
| `uv run python -m app.cli collega-doppioni` | Collega i bandi doppi tra fonte nazionale e regionali |
| `uv run python -m app.cli collaudo-prepara --file F` / `collaudo --file F --report R` | Collaudo del matching (`docs/collaudo/`) |
| `uv run python -m app.cli ruota-chiavi` | Ricifra i segreti dopo il cambio di `SECRET_KEY` |

I test di integrazione creano un database con nome casuale a partire da `TEST_DATABASE_URL`, applicano le migrazioni e lo eliminano alla fine.

## Architettura

```
app/
  main.py        FastAPI, router, /health
  config.py      Settings da variabili d'ambiente
  db.py          engine, sessioni, Base dei modelli e mixin dei timestamp
  cli.py         comandi da riga di comando (vedi tabella sopra)
  models/        modelli SQLAlchemy (auth, impostazioni, bandi, jobs)
  sources/       fonti dei bandi: download robusto, parser, runner comune
  bandi/         stato dei bandi, pagine Bandi e Fonti
  auth/          password argon2id, TOTP, sessioni, CSRF, pagine di login
  sicurezza/     cifratura dei segreti (Fernet + HKDF da SECRET_KEY)
  integrazioni/  Impostazioni → Integrazioni: registro, salvataggio, verifiche
  templates/     Jinja2 + HTMX
  static/        foglio di stile, font (Schibsted Grotesk) e htmx salvati nel repository (nessun CDN)
  jobs/          worker APScheduler (raccolte, AI, email del mattino), lock, registro job_run
  clienti/       CRM: clienti, referenti, progetti, attività, scadenze, de minimis
  importer/      import dei clienti da CSV
  pratiche/      pratiche a fasi, modelli, scadenzario, prefattibilità, statistiche
  extraction/    estrazione dei requisiti con l'AI, revisione, budget
  opportunita/   abbinamento a regole, punteggio AI, feedback
  aggiornamenti/ aggiornamenti ai clienti: bozze, canali email e WhatsApp, disiscrizione
  report/        report PDF del bando (WeasyPrint)
  appalti/       TED, Consip, ANAC, scheda gara
  documenti/     PDF dei bandi
  notifiche.py   email del mattino
  collaudo.py    collaudo del matching
  esportazione.py export completo dei dati
migrations/      Alembic (una migrazione per fase)
tests/unit       funzioni pure, nessuna rete né database
tests/integration  Postgres + pgvector reale
docs/            decisioni, schema dati, dati in uscita, fonti
docker/          Dockerfile (app e worker usano la stessa immagine)
```

- **app**: serve l'interfaccia web e `/health`.
- **worker**: esegue i job pianificati. Ogni job prende un advisory lock di Postgres, quindi due esecuzioni non si sovrappongono mai.

## Documentazione

- `docs/decisioni.md`: registro delle decisioni, con data e motivo.
- `docs/schema.md`: schema dati completo (confermato in fase 0, implementato fase per fase).
- `docs/dati-in-uscita.md`: quali dati escono dal server e verso chi.
- `docs/manuale.md`: manuale d'uso per il consulente.
- `docs/messa-in-produzione.md` e `docs/ripristino.md`: installazione, backup e ripristino.
- `docs/collaudo/`: procedura e set proposto per il collaudo prima della consegna.
- `docs/fonti/incentivi-italia.md`: com'è fatta davvero la fonte nazionale.
- `docs/fonti/regioni.md`: fonti regionali verificate (Toscana, Lazio, Umbria, Marche).

## Fonte dei dati sui bandi

Dati di [Incentivi Italia](https://www.incentivitalia.gov.it/open-data), licenza [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). I dati vengono rielaborati.
