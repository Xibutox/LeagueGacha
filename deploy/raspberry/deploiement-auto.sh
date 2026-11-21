#!/usr/bin/env bash
# Déploiement automatique : le Pi suit les tags vX.Y.Z publiés sur GitHub.
#
# Lancé toutes les 5 minutes par le timer systemd leaguegacha-deploiement
# (activer-deploiement-auto.sh). Le Pi va chercher les nouveautés lui-même :
# aucun accès entrant, aucun secret.
#
# Pour le dernier tag vX.Y.Z (ordre des versions) pas encore déployé :
#   1. il attend que le job « tests » de GitHub Actions soit vert pour ce
#      commit (un tag rouge n'est jamais déployé) ;
#   2. il sauvegarde la base et les images ;
#   3. il passe le code sur le tag, reconstruit et relance la stack
#      (migrations et boosters types au démarrage) ;
#   4. il vérifie que le site répond ; sinon, il revient à la version
#      d'avant (le code ; la sauvegarde de l'étape 2 garde les données).
#
# Usage manuel : ./deploy/raspberry/deploiement-auto.sh [--sans-ci] [vX.Y.Z]
set -euo pipefail
cd "$(dirname "$0")/../.."

SKIP_CI=""
WANTED=""
for arg in "$@"; do
	case "$arg" in
		--sans-ci) SKIP_CI=1 ;;
		v*) WANTED="$arg" ;;
	esac
done

STATE_FILE=".tag-deploye"
REFUSED_FILE=".tag-refuse"
COMPOSE=(docker compose -f compose.prod.yaml --env-file .env.prod)
docker info >/dev/null 2>&1 || COMPOSE=(sudo "${COMPOSE[@]}")
log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

# Un seul déploiement à la fois.
exec 9>/tmp/leaguegacha-deploiement.lock
flock -n 9 || exit 0

git fetch --quiet --tags --force origin
TAG="${WANTED:-$(git tag -l 'v*' --sort=-v:refname | head -n 1)}"
[ -n "$TAG" ] || exit 0
[ "$TAG" != "$(cat "$STATE_FILE" 2>/dev/null || true)" ] || exit 0
[ "$TAG" != "$(cat "$REFUSED_FILE" 2>/dev/null || true)" ] || exit 0
SHA="$(git rev-list -n 1 "$TAG")"

if [ -z "$SKIP_CI" ]; then
	REPO="$(git remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
	CONCLUSION="$(curl -fsS -H 'Accept: application/vnd.github+json' \
		"https://api.github.com/repos/$REPO/commits/$SHA/check-runs?check_name=tests" \
		| python3 -c 'import json, sys
runs = json.load(sys.stdin).get("check_runs", [])
print((runs[0].get("conclusion") or runs[0].get("status")) if runs else "absent")')" || CONCLUSION="injoignable"
	case "$CONCLUSION" in
		success) ;;
		failure|cancelled|timed_out)
			log "$TAG : tests GitHub en échec ($CONCLUSION), pas de déploiement."
			echo "$TAG" > "$REFUSED_FILE"
			exit 0 ;;
		*)
			# En cours, pas encore lancés, ou GitHub injoignable : on réessaie au prochain passage.
			log "$TAG : tests GitHub pas encore verts ($CONCLUSION), nouvel essai dans 5 minutes."
			exit 0 ;;
	esac
fi

LAN_HOST="$(sed -n 's/^LAN_HOST=//p' .env.prod)"
healthy() {
	local code
	for _ in $(seq 1 72); do
		code="$(curl -sk -o /dev/null -w '%{http_code}' "https://$LAN_HOST:8443/" || true)"
		[ "$code" = "200" ] && return 0
		sleep 5
	done
	return 1
}

PREVIOUS="$(git rev-parse HEAD)"
log "Déploiement de $TAG ($SHA), depuis $(git describe --tags --always "$PREVIOUS")."
bash ./deploy/raspberry/sauvegarde.sh

deploy_ref() {
	git -c advice.detachedHead=false checkout --quiet --force "$1"
	git submodule update --init --recursive --quiet
	# La version affichée en bas du site.
	APP_VERSION="$(git describe --tags --always)" "${COMPOSE[@]}" up -d --build --remove-orphans
}

if deploy_ref "$TAG" && healthy; then
	echo "$TAG" > "$STATE_FILE"
	rm -f "$REFUSED_FILE"
	"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
	docker image prune -f >/dev/null 2>&1 || true
	log "$TAG déployé : le site répond."
else
	log "ÉCHEC du déploiement de $TAG : retour à $PREVIOUS."
	"${COMPOSE[@]}" logs --tail 40 migrate web || true
	echo "$TAG" > "$REFUSED_FILE"
	if deploy_ref "$PREVIOUS" && healthy; then
		log "Version précédente rétablie. Les données d'avant sont dans backups/ si une migration a posé problème."
	else
		log "La version précédente ne répond pas non plus : intervention nécessaire."
	fi
	exit 1
fi
