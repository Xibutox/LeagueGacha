#!/usr/bin/env bash
# Installe et sécurise un Raspberry Pi pour héberger Wiki LoL Esport.
#
# À lancer sur le Pi, en tant que « pi » (sudo est demandé quand il faut) :
#     bash install.sh
# Le plus simple : installer-depuis-windows.bat, sur le PC, s'en charge.
#
# Relançable sans risque : chaque étape vérifie ce qui est déjà fait.
#
# Ce que le script fait :
#   1. mises à jour du système, et mises à jour de sécurité automatiques ;
#   2. Docker et Docker Compose (dépôt officiel de Docker) ;
#   3. SSH : connexion par clé seulement, pas de root, réseau local seulement ;
#   4. pare-feu (ufw) : 80 et 443 ouverts à tous, SSH et 8443 au réseau local,
#      tout le reste fermé ; fail2ban contre les essais de mots de passe ;
#   5. le projet depuis GitHub, ses secrets (.env.prod) et la stack de prod ;
#   6. une sauvegarde de la base chaque nuit (14 jours gardés).
#
# Variables facultatives (sinon le script pose la question) :
#   REPO_URL      dépôt GitHub (https://… ou git@github.com:…)
#   SITE_DOMAIN   nom de domaine public (ex. monjeu.freeboxos.fr)
#   ACME_EMAIL    e-mail pour Let's Encrypt
#   APP_DIR       dossier du projet (défaut : /opt/wiki-lol-esport)

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wiki-lol-esport}"
COMPOSE=(docker compose -f compose.prod.yaml --env-file .env.prod)

bold() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m[ERREUR] %s\033[0m\n' "$*" >&2; exit 1; }
ask() {  # ask VAR "Question" [défaut]
	local var="$1" question="$2" default="${3:-}" answer
	if [ -n "${!var:-}" ]; then return; fi
	if [ -n "$default" ]; then question="$question [$default]"; fi
	read -r -p "$question : " answer
	printf -v "$var" '%s' "${answer:-$default}"
}

[ "$(id -u)" -ne 0 ] || fail "Lance ce script en tant que pi, pas root (sudo est appelé quand il faut)."
sudo -v || fail "sudo est nécessaire."

# ------------------------------------------------------------ Vérifications
bold "Vérifications"
ARCH="$(dpkg --print-architecture)"
. /etc/os-release
echo "Système : $PRETTY_NAME ($ARCH)"
case "$ARCH" in
	arm64|amd64) ;;
	armhf) fail "Système 32 bits : installe Raspberry Pi OS 64 bits (Lite suffit), les images Docker du projet sont en 64 bits." ;;
	*) fail "Architecture non prévue : $ARCH" ;;
esac
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
[ "$MEM_MB" -ge 1800 ] || warn "Seulement ${MEM_MB} Mo de mémoire : prévois au moins 2 Go (Pi 4 ou 5)."

LAN_DEV="$(ip -4 route show default | awk '{print $5; exit}')"
LAN_IP="$(ip -4 -o addr show dev "$LAN_DEV" | awk '{split($4, a, "/"); print a[1]; exit}')"
LAN_NET="$(ip -4 route show dev "$LAN_DEV" scope link | awk '{print $1; exit}')"
[ -n "$LAN_IP" ] && [ -n "$LAN_NET" ] || fail "Réseau local introuvable."
echo "Réseau local : $LAN_NET (le Pi : $LAN_IP sur $LAN_DEV)"

# Une installation déjà commencée : on reprend ses réponses.
if [ -f "$APP_DIR/.env.prod" ]; then
	SITE_DOMAIN="${SITE_DOMAIN:-$(sed -n 's/^SITE_DOMAIN=//p' "$APP_DIR/.env.prod")}"
	ACME_EMAIL="${ACME_EMAIL:-$(sed -n 's/^ACME_EMAIL=//p' "$APP_DIR/.env.prod")}"
fi
if [ -d "$APP_DIR/.git" ]; then
	REPO_URL="${REPO_URL:-$(git -C "$APP_DIR" remote get-url origin)}"
fi
ask REPO_URL "Dépôt GitHub du projet (https://github.com/… ou git@github.com:…)"
ask SITE_DOMAIN "Nom de domaine public (ex. monjeu.freeboxos.fr)"
ask ACME_EMAIL "E-mail pour les certificats Let's Encrypt"
[ -n "$REPO_URL" ] && [ -n "$SITE_DOMAIN" ] && [ -n "$ACME_EMAIL" ] || fail "Dépôt, domaine et e-mail sont nécessaires."

# ------------------------------------------------------------ Mot de passe
if sudo grep -q '^pi:' /etc/shadow && [ -t 0 ]; then
	bold "Mot de passe de pi"
	read -r -p "Changer le mot de passe de pi maintenant ? Fortement conseillé s'il vaut encore « raspberry » [O/n] " CHANGE
	if [ "${CHANGE:-O}" != "n" ] && [ "${CHANGE:-O}" != "N" ]; then
		passwd
	fi
fi

# ------------------------------------------------------------ Système
bold "Mises à jour du système"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get -y full-upgrade
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install \
	ca-certificates curl gnupg git ufw fail2ban unattended-upgrades apt-listchanges openssl

bold "Mises à jour de sécurité automatiques"
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
sudo systemctl enable --now unattended-upgrades >/dev/null

# ------------------------------------------------------------ Docker
# Le dépôt officiel de Docker, même si Docker est déjà là : il fournit
# Compose (« docker compose ») et Buildx, absents du Docker de Debian.
bold "Docker (dépôt officiel)"
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
	| sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
if ! command -v docker >/dev/null; then
	sudo DEBIAN_FRONTEND=noninteractive apt-get -y install \
		docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif ! sudo docker compose version >/dev/null 2>&1; then
	# Le site est lancé avec sudo : Compose doit être installé pour tout le
	# système, pas seulement dans ~/.docker de l'utilisateur.
	echo "Docker est là, sans Compose pour tout le système : ajout de Compose et Buildx."
	echo "  docker : $(command -v docker) ; plugins : $(ls /usr/libexec/docker/cli-plugins /usr/lib/docker/cli-plugins "$HOME/.docker/cli-plugins" 2>/dev/null | tr '\n' ' ')"
	sudo DEBIAN_FRONTEND=noninteractive apt-get -y install docker-compose-plugin docker-buildx-plugin \
		|| sudo DEBIAN_FRONTEND=noninteractive apt-get -y install docker-compose
fi
sudo docker compose version || fail "Docker Compose reste introuvable pour root (sudo docker compose)."
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'CONF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true
}
CONF
sudo systemctl enable --now docker >/dev/null
sudo systemctl restart docker
sudo usermod -aG docker "$USER"
DOCKER=(sudo docker)

# ------------------------------------------------------------ SSH
bold "SSH : clé seulement, réseau local seulement"
if [ -s "$HOME/.ssh/authorized_keys" ]; then
	sudo tee /etc/ssh/sshd_config.d/90-wiki-lol.conf >/dev/null <<CONF
# Posé par install.sh (Wiki LoL Esport).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PermitEmptyPasswords no
AllowUsers $USER
MaxAuthTries 3
X11Forwarding no
CONF
	sudo sshd -t && sudo systemctl reload ssh
	echo "Connexion par mot de passe désactivée : ta clé SSH suffit."
else
	warn "Aucune clé dans ~/.ssh/authorized_keys : la connexion par mot de passe reste ouverte."
	warn "Lance installer-depuis-windows.bat depuis ton PC, il ajoute ta clé."
fi

# ------------------------------------------------------------ Pare-feu
bold "Pare-feu (ufw)"
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null
sudo ufw allow from "$LAN_NET" to any port 22 proto tcp comment "SSH, réseau local" >/dev/null
sudo ufw allow 80/tcp comment "site (HTTP -> HTTPS)" >/dev/null
sudo ufw allow 443/tcp comment "site (HTTPS)" >/dev/null
sudo ufw allow from "$LAN_NET" to any port 8443 proto tcp comment "gestion, réseau local" >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status verbose

# Docker publie ses ports sans passer par ufw : la chaîne DOCKER-USER
# refait la même règle pour les conteneurs (8443 réservé au réseau local).
if ! sudo grep -q "WIKI-LOL DOCKER-USER" /etc/ufw/after.rules; then
	sudo tee -a /etc/ufw/after.rules >/dev/null <<CONF

# WIKI-LOL DOCKER-USER : les ports publiés par Docker, hors réseau local,
# seulement 80 et 443.
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
-A DOCKER-USER -s $LAN_NET -j RETURN
-A DOCKER-USER -s 172.16.0.0/12 -j RETURN
-A DOCKER-USER -i $LAN_DEV -p tcp -m conntrack --ctorigdstport 80 -j RETURN
-A DOCKER-USER -i $LAN_DEV -p tcp -m conntrack --ctorigdstport 443 -j RETURN
-A DOCKER-USER -i $LAN_DEV -j DROP
-A DOCKER-USER -j RETURN
COMMIT
CONF
fi
sudo ufw reload >/dev/null

bold "fail2ban (SSH)"
sudo tee /etc/fail2ban/jail.d/wiki-lol.local >/dev/null <<'CONF'
[sshd]
enabled = true
backend = systemd
maxretry = 4
findtime = 10m
bantime = 1h
CONF
sudo systemctl enable --now fail2ban >/dev/null
sudo systemctl restart fail2ban

# ------------------------------------------------------------ Le projet
bold "Le projet"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"
if [[ "$REPO_URL" == git@* ]]; then
	KEY="$HOME/.ssh/github_deploy"
	if [ ! -f "$KEY" ]; then
		ssh-keygen -t ed25519 -N "" -C "raspberry-wiki-lol" -f "$KEY" >/dev/null
	fi
	if ! grep -q "github.com" "$HOME/.ssh/config" 2>/dev/null; then
		printf 'Host github.com\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$KEY" >> "$HOME/.ssh/config"
		chmod 600 "$HOME/.ssh/config"
	fi
	ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
	if ! ssh -o BatchMode=yes -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
		echo
		echo "Dépôt privé : ajoute cette clé dans GitHub, dépôt › Settings › Deploy keys › Add deploy key"
		echo "(lecture seule, ne coche pas « Allow write access ») :"
		echo
		cat "$KEY.pub"
		echo
		read -r -p "Appuie sur Entrée une fois la clé ajoutée… " _
	fi
fi
if [ -d "$APP_DIR/.git" ]; then
	git -C "$APP_DIR" pull --ff-only
	git -C "$APP_DIR" submodule update --init --recursive
else
	git clone --recurse-submodules "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [ ! -f .env.prod ]; then
	bold "Secrets (.env.prod)"
	umask 077
	cat > .env.prod <<CONF
# Créé par install.sh — ne pas publier. Les secrets sont tirés au hasard.
SITE_DOMAIN=$SITE_DOMAIN
LAN_HOST=$LAN_IP
ACME_EMAIL=$ACME_EMAIL
POSTGRES_PASSWORD=$(openssl rand -hex 24)
DJANGO_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')
DJANGO_ALLOWED_HOSTS=$SITE_DOMAIN,$LAN_IP
DJANGO_CSRF_TRUSTED_ORIGINS=https://$SITE_DOMAIN,https://$LAN_IP:8443
DJANGO_HSTS_SECONDS=31536000
# Le jeu
GAME_STAFF_UNLIMITED=True
DDRAGON_AUTO_SYNC=True
LEAGUEPEDIA_USER_AGENT=WikiLolEsport/1.0 (contact: $ACME_EMAIL)
CONF
	umask 022
else
	echo ".env.prod existe déjà : gardé tel quel."
fi

bold "Construction et démarrage (la première fois : 10 à 20 minutes sur un Pi)"
"${DOCKER[@]}" "${COMPOSE[@]}" up -d --build
"${DOCKER[@]}" "${COMPOSE[@]}" ps

# ------------------------------------------------------------ Sauvegardes
bold "Sauvegarde de la base chaque nuit"
chmod +x deploy/raspberry/*.sh
sudo tee /etc/cron.d/wiki-lol-sauvegarde >/dev/null <<CONF
# Sauvegarde de la base de Wiki LoL Esport, chaque nuit à 3 h 17.
17 3 * * * $USER $APP_DIR/deploy/raspberry/sauvegarde.sh >> $APP_DIR/backups/sauvegarde.log 2>&1
CONF
mkdir -p backups

# ------------------------------------------------------------ Fin
bold "Terminé"
cat <<FIN
Le site       : https://$SITE_DOMAIN/   (une fois la Freebox configurée)
Réseau local  : https://$LAN_IP:8443/   (gestion : /gestion/ ; le navigateur
                avertit sur le certificat local la première fois)

Il reste :
  - créer le compte administrateur :
      cd $APP_DIR && sudo docker compose -f compose.prod.yaml --env-file .env.prod exec web python manage.py createsuperuser
  - régler la Freebox (README, « Héberger sur un Raspberry Pi ») ;
  - depuis la 4G, vérifier que https://$SITE_DOMAIN/gestion/ répond « Accès réservé au réseau local ».
FIN
