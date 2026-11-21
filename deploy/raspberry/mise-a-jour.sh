#!/usr/bin/env bash
# Met le site à jour depuis GitHub : récupère le code, reconstruit l'image,
# relance (les migrations passent au démarrage). Une sauvegarde d'abord.
set -euo pipefail
cd "$(dirname "$0")/../.."
SUDO=""; docker info >/dev/null 2>&1 || SUDO="sudo"
./deploy/raspberry/sauvegarde.sh
git pull --ff-only
git submodule update --init --recursive
$SUDO docker compose -f compose.prod.yaml --env-file .env.prod up -d --build
$SUDO docker image prune -f >/dev/null
echo "Site à jour."
