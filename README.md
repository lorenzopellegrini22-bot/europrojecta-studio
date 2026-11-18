# Europrojecta Studio

Gestionale clienti e bandi per la consulenza di finanza agevolata. Software di NuvioLab, concesso in licenza d'uso: un'istanza per cliente.

Ha tre parti:
1. **CRM verticale**: aziende clienti, referenti, progetti d'investimento, pratiche, attività e scadenze.
2. **Motore di matching**: ogni notte raccoglie i bandi nazionali e regionali, li struttura con l'LLM e segnala per quali aziende sono compatibili, spiegando il perché.
3. **Comunicazione**: email del mattino al consulente e aggiornamenti ai suoi clienti, ognuno approvato da lui.

> Stato: **fase 0 completata** più accesso sicuro (password + 2FA) e **Impostazioni → Integrazioni** (chiavi API di OpenAI, SMTP e WhatsApp, cifrate). Le fasi sono descritte nel brief di progetto; le scelte in `docs/decisioni.md`.

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

Python 3.12, FastAPI (endpoint sincroni), PostgreSQL 16 + pgvector, SQLAlchemy 2 + Alembic, Jinja2 + HTMX, APScheduler nel worker, uv / ruff / pytest, Docker Compose.

## Avvio in sviluppo

Prerequisiti: [uv](https://docs.astral.sh/uv/) e Docker.

```bash
cp .env.example .env          # poi compila POSTGRES_PASSWORD e SECRET_KEY
uv sync                       # crea .venv con Python 3.12 e le dipendenze
docker compose up -d --build  # db + app (migrazioni automatiche) + worker
curl http://localhost:8000/health
```

Senza Docker per l'app (solo il db in container):

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

I test di integrazione creano un database con nome casuale a partire da `TEST_DATABASE_URL`, applicano le migrazioni e lo eliminano alla fine.

## Architettura

```
app/
  main.py        FastAPI, router, /health
  config.py      Settings da variabili d'ambiente
  db.py          engine, sessioni, Base dei modelli e mixin dei timestamp
  cli.py         comandi: crea-utente, reimposta-2fa
  models/        modelli SQLAlchemy (auth, impostazioni, …)
  auth/          password argon2id, TOTP, sessioni, CSRF, pagine di login
  sicurezza/     cifratura dei segreti (Fernet + HKDF da SECRET_KEY)
  integrazioni/  Impostazioni → Integrazioni: registro, salvataggio, verifiche
  templates/     Jinja2 + HTMX
  static/        Pico.css e htmx salvati nel repository (nessun CDN)
  jobs/          worker APScheduler e advisory lock di Postgres
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
- `docs/fonti/incentivi-italia.md`: com'è fatta davvero la fonte nazionale.

## Fonte dei dati sui bandi

Dati di [Incentivi Italia](https://www.incentivitalia.gov.it/open-data), licenza [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). I dati vengono rielaborati.
