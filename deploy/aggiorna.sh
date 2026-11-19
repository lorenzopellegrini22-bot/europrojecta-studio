#!/usr/bin/env bash
# Aggiornamento automatico del server dal ramo main di GitHub.
#
# Lo lancia ogni 2 minuti il timer systemd europrojecta-aggiorna.timer (vedi
# docs/messa-in-produzione.md). Se su GitHub c'è un commit nuovo e i controlli della CI
# sono tutti verdi, scarica il codice e riavvia i servizi: le migrazioni del database
# partono da sole all'avvio dell'app. Con la CI rossa o ancora in corso non tocca niente.
# Il server non ha nessuna credenziale GitHub: il repository è pubblico e l'API dei
# controlli si legge senza token.
set -euo pipefail

CARTELLA="${CARTELLA:-/opt/europrojecta}"
RAMO="${RAMO:-main}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

cd "$CARTELLA"
exec 9>/run/europrojecta-aggiorna.lock
flock -n 9 || exit 0

git fetch --quiet origin "$RAMO"
attuale=$(git rev-parse HEAD)
nuovo=$(git rev-parse "origin/$RAMO")
[ "$attuale" = "$nuovo" ] && exit 0

repo=$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
controlli=$(curl -fsS -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$repo/commits/$nuovo/check-runs?per_page=100")
totale=$(jq '.check_runs | length' <<<"$controlli")
in_corso=$(jq '[.check_runs[] | select(.status != "completed")] | length' <<<"$controlli")
falliti=$(jq '[.check_runs[] | select(.status == "completed")
  | select(.conclusion as $c | ["success", "skipped", "neutral"] | index($c) | not)] | length' \
  <<<"$controlli")

if [ "$totale" -eq 0 ] || [ "$in_corso" -gt 0 ]; then
  exit 0 # CI non ancora partita o in corso: si riprova al giro dopo
fi
if [ "$falliti" -gt 0 ]; then
  segno="/run/europrojecta-ci-rossa-$nuovo"
  [ -e "$segno" ] || { echo "CI rossa su ${nuovo:0:7}: aggiornamento saltato"; touch "$segno"; }
  exit 0
fi

echo "Aggiorno ${attuale:0:7} -> ${nuovo:0:7}: $(git log -1 --format=%s "$nuovo")"
git reset --hard --quiet "$nuovo"
"${COMPOSE[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null
echo "Aggiornato a ${nuovo:0:7}"
