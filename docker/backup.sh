#!/bin/bash
# Backup cifrati con restic (brief, sezione 13): database + allegati.
#   backup.sh ora       -> un backup subito, poi pulizia secondo la retention
#   backup.sh verifica  -> prova di ripristino: ripristina l'ultimo dump in un database
#                          temporaneo, controlla le tabelle e lo elimina; poi «restic check»
#   backup.sh ciclo     -> servizio Docker: backup ogni giorno all'ora BACKUP_ORARIO e
#                          prova di ripristino il primo giorno di ogni mese
#
# Variabili: RESTIC_REPOSITORY (es. /backup/restic, s3:..., sftp:..., b2:...),
# RESTIC_PASSWORD (la passphrase: senza non si apre nessun backup, conservala fuori dal
# server), PGHOST/PGUSER/PGPASSWORD/PGDATABASE, BACKUP_ORARIO, e le credenziali dello
# storage esterno se servono (es. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).
# Retention: 7 copie giornaliere, 4 settimanali, 12 mensili.
set -euo pipefail  # pipefail: se pg_dump fallisce il backup risulta fallito

: "${RESTIC_REPOSITORY:?manca RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?manca RESTIC_PASSWORD}"
ORARIO="${BACKUP_ORARIO:-01:30}"

log() { echo "$(date '+%F %T') $*"; }

prepara_repository() {
    restic cat config >/dev/null 2>&1 || restic init
}

backup() {
    prepara_repository
    pg_dump --format=custom --no-owner \
        | restic backup --stdin --stdin-filename europrojecta.dump --tag db --host europrojecta
    if [ -d /dati/allegati ]; then
        restic backup /dati/allegati --tag allegati --host europrojecta
    fi
    restic forget --prune --host europrojecta --group-by host,tags \
        --keep-daily 7 --keep-weekly 4 --keep-monthly 12
    log "backup completato"
}

# Sottoshell: la pulizia (trap EXIT) avviene alla fine della verifica, anche nel ciclo
verifica() (
    prepara_repository
    db="verifica_ripristino_$$"
    restic dump --tag db --host europrojecta latest /europrojecta.dump > /tmp/ripristino.dump
    createdb "$db"
    trap 'dropdb --if-exists "$db"; rm -f /tmp/ripristino.dump' EXIT
    pg_restore --no-owner --dbname "$db" /tmp/ripristino.dump
    tabelle=$(psql -At -d "$db" -c "select count(*) from information_schema.tables where table_schema = 'public'")
    if [ "$tabelle" -lt 1 ]; then
        log "PROVA DI RIPRISTINO FALLITA: nessuna tabella ripristinata"
        return 1
    fi
    aziende=$(psql -At -d "$db" -c "select count(*) from azienda" 2>/dev/null || echo "n.d.")
    restic check
    log "prova di ripristino riuscita: $tabelle tabelle, $aziende aziende"
)

case "${1:-ora}" in
    ora) backup ;;
    verifica) verifica ;;
    ciclo)
        log "Backup ogni giorno alle $ORARIO, prova di ripristino il primo del mese"
        while true; do
            if [ "$(date +%H:%M)" = "$ORARIO" ]; then
                backup || log "BACKUP NON RIUSCITO" >&2
                if [ "$(date +%d)" = "01" ]; then
                    verifica || log "PROVA DI RIPRISTINO NON RIUSCITA" >&2
                fi
                sleep 61
            fi
            sleep 30
        done
        ;;
    *) echo "uso: backup.sh [ora|verifica|ciclo]" >&2; exit 2 ;;
esac
