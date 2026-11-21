#!/usr/bin/env bash
# Met le site à jour tout de suite, sans attendre le timer :
#   ./deploy/raspberry/mise-a-jour.sh            le dernier tag vX.Y.Z aux tests verts
#   ./deploy/raspberry/mise-a-jour.sh v1.2.0     un tag précis
#   ./deploy/raspberry/mise-a-jour.sh --main     la branche main (pour essayer avant un tag)
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ "${1:-}" = "--main" ]; then
	COMPOSE=(docker compose -f compose.prod.yaml --env-file .env.prod)
	docker info >/dev/null 2>&1 || COMPOSE=(sudo "${COMPOSE[@]}")
	bash ./deploy/raspberry/sauvegarde.sh
	git fetch --quiet origin
	git -c advice.detachedHead=false checkout --quiet --force origin/main
	git submodule update --init --recursive --quiet
	"${COMPOSE[@]}" up -d --build --remove-orphans
	rm -f .tag-deploye
	echo "Site sur main ($(git rev-parse --short HEAD)). Le prochain tag le remplacera."
else
	bash ./deploy/raspberry/deploiement-auto.sh "$@"
fi
