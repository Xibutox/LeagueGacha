#!/usr/bin/env bash
# Active le déploiement automatique : un timer systemd lance
# deploiement-auto.sh toutes les 5 minutes (le Pi suit les tags vX.Y.Z dont
# les tests GitHub sont verts). À lancer une fois sur le Pi :
#     bash deploy/raspberry/activer-deploiement-auto.sh
# Journal : journalctl -u leaguegacha-deploiement -f
set -euo pipefail
cd "$(dirname "$0")/../.."
APP_DIR="$(pwd)"
RUN_AS="${SUDO_USER:-$USER}"

sudo usermod -aG docker "$RUN_AS"
sudo tee /etc/systemd/system/leaguegacha-deploiement.service >/dev/null <<CONF
[Unit]
Description=LeagueGacha : déploiement automatique des tags GitHub
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/bash $APP_DIR/deploy/raspberry/deploiement-auto.sh
TimeoutStartSec=1h
CONF
sudo tee /etc/systemd/system/leaguegacha-deploiement.timer >/dev/null <<'CONF'
[Unit]
Description=LeagueGacha : cherche un nouveau tag toutes les 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
CONF
sudo systemctl daemon-reload
sudo systemctl enable --now leaguegacha-deploiement.timer
systemctl list-timers leaguegacha-deploiement.timer --no-pager
echo
echo "Déploiement automatique actif. Publier une version depuis le PC :"
echo "    git tag v1.2.0 && git push origin v1.2.0"
echo "Suivre le Pi : journalctl -u leaguegacha-deploiement -f"
