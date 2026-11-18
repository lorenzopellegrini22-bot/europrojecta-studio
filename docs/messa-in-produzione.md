# Messa in produzione

Server consigliato: una VPS Linux in UE (2 vCPU, 4 GB di RAM, 40 GB di disco) con Docker e
il plugin Compose. Serve un dominio (es. `studio.esempio.it`) che punti all'IP del server.

## 1. Preparazione
```bash
git clone https://github.com/lorenzopellegrini22-bot/europrojecta-studio.git
cd europrojecta-studio
cp .env.example .env
```
Nel file `.env` imposta almeno:
- `POSTGRES_PASSWORD`: password lunga e casuale.
- `SECRET_KEY`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `DOMINIO`: il dominio dell'app; `EMAIL_CERTIFICATI`: email per Let's Encrypt.
- `BACKUP_PASSPHRASE`: passphrase dei backup (restic). **Conservala anche fuori dal server**
  (gestore di password): senza, i backup non si aprono.

## 2. Avvio
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec app python -m app.cli crea-utente --email titolare@studio.it --nome "Nome" --ruolo titolare
```
Caddy ottiene da solo il certificato https. Al primo accesso l'utente configura la 2FA.

Servizi in produzione:
| Servizio | Cosa fa |
|---|---|
| `caddy` | https, unico servizio esposto su Internet (porte 80 e 443) |
| `app` | l'applicazione web (porta 8000 solo verso Caddy) |
| `worker` | job notturni e del mattino |
| `db` | PostgreSQL 16 + pgvector, non esposto |
| `clamav` | antivirus: ogni file caricato viene controllato prima di essere salvato |
| `backup` | backup restic cifrati ogni notte (01:30), retention 7/4/12, prova di ripristino mensile |

## 3. Backup
- Ogni notte con **restic**: dump del database e allegati, cifrati; retention 7 giornalieri,
  4 settimanali, 12 mensili; prova di ripristino automatica il primo di ogni mese.
- Imposta nel `.env` uno **storage esterno** in `RESTIC_REPOSITORY` (S3, Backblaze B2, SFTP)
  con le sue credenziali: per default i backup restano in `./backup/restic` sul server.
- Comandi e ripristino completo: **`docs/ripristino.md`**.

## 4. Antivirus
Gli allegati delle pratiche e i PDF caricati a mano passano da ClamAV. Se il file è
infetto non viene salvato; se ClamAV non risponde (es. nei primi minuti dopo l'avvio,
mentre scarica le firme) il caricamento è rifiutato con l'invito a riprovare.

## 5. Aggiornamenti
```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
Le migrazioni del database partono da sole all'avvio dell'app.

## 6. Cambio della SECRET_KEY (rotazione)
1. Nel `.env`: `SECRET_KEY` = nuova chiave, `SECRET_KEY_PRECEDENTI` = vecchia; riavvia.
2. `docker compose exec app python -m app.cli ruota-chiavi` (ricifra chiavi API e 2FA).
3. Togli `SECRET_KEY_PRECEDENTI` e riavvia.
Le sessioni vengono chiuse; ogni utente rigenera i codici di recupero da «Il mio account».
