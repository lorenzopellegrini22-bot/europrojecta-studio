#!/bin/bash
# Backup cifrato di database e allegati.
#   backup.sh ora    -> un backup subito
#   backup.sh ciclo  -> un backup ogni giorno all'ora BACKUP_ORARIO (servizio Docker)
# File prodotti in /backup (cartella ./backup sul server):
#   europrojecta-AAAAMMGG-HHMM.db.gpg        dump PostgreSQL (formato custom)
#   europrojecta-AAAAMMGG-HHMM.allegati.gpg  archivio tar degli allegati
# Cifratura: GPG simmetrico AES256 con BACKUP_PASSPHRASE. Senza la passphrase i backup
# non si possono aprire: conservala fuori dal server (gestore di password).
set -euo pipefail  # pipefail: se pg_dump fallisce il backup risulta fallito

: "${BACKUP_PASSPHRASE:?manca BACKUP_PASSPHRASE}"
GIORNI="${BACKUP_GIORNI:-14}"
ORARIO="${BACKUP_ORARIO:-01:30}"

cifra() {
    gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 --output "$1" 3<<EOF
$BACKUP_PASSPHRASE
EOF
}

backup() {
    nome="europrojecta-$(date +%Y%m%d-%H%M)"
    tmp="/backup/.$nome.tmp"
    pg_dump --format=custom --no-owner | cifra "$tmp.db"
    mv "$tmp.db" "/backup/$nome.db.gpg"
    tar -C /dati -cf - allegati | cifra "$tmp.allegati"
    mv "$tmp.allegati" "/backup/$nome.allegati.gpg"
    find /backup -maxdepth 1 -name 'europrojecta-*.gpg' -mtime +"$GIORNI" -delete
    echo "$(date '+%F %T') backup completato: $nome"
}

case "${1:-ora}" in
    ora) backup ;;
    ciclo)
        echo "Backup ogni giorno alle $ORARIO, conservati $GIORNI giorni"
        while true; do
            if [ "$(date +%H:%M)" = "$ORARIO" ]; then
                backup || echo "$(date '+%F %T') BACKUP NON RIUSCITO" >&2
                sleep 61
            fi
            sleep 30
        done
        ;;
    *) echo "uso: backup.sh [ora|ciclo]" >&2; exit 2 ;;
esac
