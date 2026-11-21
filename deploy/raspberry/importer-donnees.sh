#!/usr/bin/env bash
# Remplace la base et les images du Pi par celles exportées du PC
# (exporter-donnees.bat). Usage, sur le Pi :
#     ./deploy/raspberry/importer-donnees.sh ~/base.dump ~/fichiers.tar.gz
# Une sauvegarde de l'état actuel est faite avant.
set -euo pipefail
DUMP="${1:?Chemin du fichier base.dump}"
FILES="${2:-}"
cd "$(dirname "$0")/../.."
SUDO=""; docker info >/dev/null 2>&1 || SUDO="sudo"
COMPOSE=($SUDO docker compose -f compose.prod.yaml --env-file .env.prod)

bash ./deploy/raspberry/sauvegarde.sh || true
"${COMPOSE[@]}" stop web worker caddy
"${COMPOSE[@]}" exec -T db pg_restore -U wikilol -d wikilol --clean --if-exists --no-owner < "$DUMP"
if [ -n "$FILES" ]; then
	"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint "" web tar -C /app -xzf - < "$FILES"
fi
"${COMPOSE[@]}" up -d
echo "Données importées."
