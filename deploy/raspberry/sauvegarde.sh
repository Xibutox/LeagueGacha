#!/usr/bin/env bash
# Sauvegarde la base (pg_dump) et les images du jeu dans backups/.
# Lancée chaque nuit par cron (install.sh) ; les 14 dernières sont gardées.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p backups
STAMP="$(date +%Y-%m-%d_%H%M)"
COMPOSE=(docker compose -f compose.prod.yaml --env-file .env.prod)
SUDO=""; docker info >/dev/null 2>&1 || SUDO="sudo"

$SUDO "${COMPOSE[@]}" exec -T db pg_dump -U wikilol -Fc wikilol > "backups/base_$STAMP.dump"
$SUDO "${COMPOSE[@]}" exec -T web tar -C /app -czf - game_media media > "backups/fichiers_$STAMP.tar.gz"
ls -1t backups/base_*.dump | tail -n +15 | xargs -r rm --
ls -1t backups/fichiers_*.tar.gz | tail -n +15 | xargs -r rm --
echo "$(date '+%F %T') sauvegarde $STAMP faite"
