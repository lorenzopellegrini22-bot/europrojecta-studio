# Backup e ripristino

## Come sono fatti i backup
- **Ogni notte (01:30)** il servizio `backup` salva con **restic** il dump del database e la
  cartella degli allegati, cifrati con la passphrase `BACKUP_PASSPHRASE`.
- **Retention:** 7 copie giornaliere, 4 settimanali, 12 mensili (`restic forget --prune`).
- **Dove:** `RESTIC_REPOSITORY`. Per default `./backup/restic` sul server; in produzione va
  impostato uno **storage esterno** (es. `s3:https://s3.eu-central-1.amazonaws.com/nome-bucket`,
  `b2:nome-bucket:europrojecta`, `sftp:utente@server:/backup/europrojecta`), con le
  credenziali nel `.env` (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` o `B2_ACCOUNT_ID`/
  `B2_ACCOUNT_KEY`). Un backup solo sullo stesso disco non protegge da un guasto del server.
- **Prova di ripristino automatica il primo di ogni mese:** l'ultimo dump viene ripristinato
  in un database temporaneo, si contano tabelle e aziende, poi `restic check` controlla
  l'integrità del repository. L'esito è nel log del servizio:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backup`.
- La CI prova a ogni commit backup, ripristino e rifiuto della passphrase sbagliata.

**La passphrase va conservata anche fuori dal server** (gestore di password del consulente
e di NuvioLab): senza, i backup non si possono aprire.

## Comandi
In tutti gli esempi `C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`.

| Cosa | Comando |
|---|---|
| Backup subito | `$C exec backup /usr/local/bin/backup.sh ora` |
| Prova di ripristino subito | `$C exec backup /usr/local/bin/backup.sh verifica` |
| Elenco dei backup | `$C exec backup restic snapshots` |

## Ripristino completo (es. nuovo server)
1. Installare l'app come in `docs/messa-in-produzione.md`, con lo stesso `.env`
   (stessa `SECRET_KEY`, altrimenti chiavi API e 2FA vanno reinserite).
2. Avviare solo database e backup:
   `$C up -d db backup`
3. Ripristinare il database dall'ultimo backup:
   ```bash
   $C exec backup sh -c 'restic dump --tag db latest /europrojecta.dump > /tmp/db.dump && pg_restore --clean --if-exists --no-owner -d "$PGDATABASE" /tmp/db.dump'
   ```
4. Ripristinare gli allegati nel volume:
   ```bash
   $C run --rm -v europrojecta-studio_allegati:/ripristino backup sh -c 'restic restore latest --tag allegati --target /tmp/r && cp -a /tmp/r/dati/allegati/. /ripristino/'
   ```
5. Avviare tutto: `$C up -d`. Controllare login, un cliente con allegati e la pagina Fonti.

Per tornare a un giorno preciso: `restic snapshots` e usare l'ID al posto di `latest`.
