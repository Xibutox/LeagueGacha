#!/usr/bin/env bash
# L'état du site sur le Pi : version déployée, dernière version publiée,
# prochain passage du déploiement automatique, conteneurs.
#     bash deploy/raspberry/statut.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f compose.prod.yaml --env-file .env.prod)
docker info >/dev/null 2>&1 || COMPOSE=(sudo "${COMPOSE[@]}")

git fetch --quiet --tags origin 2>/dev/null
echo "Version déployée   : $(cat .tag-deploye 2>/dev/null || echo 'aucun tag (main)')"
echo "Code sur le disque : $(git describe --tags --always)"
echo "Dernier tag publié : $(git tag -l 'v*' --sort=-v:refname | head -n 1)"
[ -f .tag-refuse ] && echo "Tag refusé         : $(cat .tag-refuse) (tests rouges ou échec, voir le journal)"
echo
systemctl list-timers leaguegacha-deploiement.timer --no-pager 2>/dev/null | head -n 2
echo
"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
echo
echo "Derniers messages du déploiement automatique :"
journalctl -u leaguegacha-deploiement --no-pager -n 400 2>/dev/null | grep -v 'systemd\[1\]' | tail -n 8
