# LeagueGacha

Jeu web de collection de cartes des joueurs pros de League of Legends : on
ouvre des boosters (qui se rechargent toutes les heures) pour collectionner
des cartes, on monte des équipes et on grimpe au classement.

En ligne : https://leaguegacha.freeboxos.fr/

- **Une carte = un joueur × une période** (« Faker — LCK 2026 » : toute
  l'année d'une ligue locale, ou un événement international).
- **Chaque joueur existe à toutes les raretés** ; les doublons se
  fusionnent, et la rareté donne un bonus en équipe.
- **Équipes** de 5 joueurs × 5 champions, notées sur les vraies parties et
  la cohérence des compositions, avec un classement.
- **Données en local** : parties, stats, identité (pseudo, nom, naissance,
  pays) et photos importées depuis [Leaguepedia](https://lol.fandom.com).
- **Gestion** sur [django-generic](https://github.com/Xibutox/django-generic)
  (sous-module `django-generic/`) : listes filtrables, résumés, exports,
  import lancé en tâche de fond avec rapport.

| Adresse | |
|---|---|
| http://localhost:8000/ | le jeu (boosters, collection, équipes, classement) |
| http://localhost:8000/gestion/ | la gestion (django-generic) |

## Installation

Sous Windows : double-clic sur **`demarrer.bat`**. Il fait les étapes ci-dessous et ouvre la gestion.

```bash
git init
git submodule add https://github.com/Xibutox/django-generic.git django-generic
copy .env.example .env          # facultatif (Linux/Mac : cp)
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

La stack (`compose.yaml`) lance :

- `db` : Postgres ;
- `redis` : Redis ;
- `migrate` : les migrations, à chaque démarrage ;
- `web` : Django avec runserver, qui recharge le code tout seul ;
- `worker` : Celery, pour les imports lancés depuis la gestion.

Pour cloner le projet ailleurs : `git clone --recurse-submodules …`.

## Importer des données

En ligne de commande, d'abord la découverte des tournois, puis l'import :

```bash
# Lister les tournois (aperçu), puis les enregistrer : crée les splits
docker compose exec web python manage.py lp_tournaments --league LEC --year 2025
docker compose exec web python manage.py lp_tournaments --league LEC --year 2025 --save

# Parties, joueurs, photos, stats et cartes
docker compose exec web python manage.py lp_import --league LEC --year 2025

# Raretés par défaut, un booster par split et un booster « Tous les splits »
docker compose exec web python manage.py init_boosters
```

Le plus simple, dans la gestion :

1. **Ligues › Synchroniser les ligues** : récupère depuis Leaguepedia
   seulement les ligues demandées (« LCK, LEC, LPL »). Laisse le champ vide
   pour tout récupérer ; la case « ligues majeures seulement » est cochée
   par défaut. Dans la liste des ligues, l'action **Importer l'année en
   cours** lance l'import des ligues cochées.
   L'action **Retirer l'import** enlève tout ce qu'une ligue a apporté
   (splits, parties, stats, cartes, y compris les exemplaires possédés) et
   garde la ligue pour la réimporter plus tard. Les boosters automatiques
   vidés sont supprimés, ceux faits à la main qui perdent leur filtre sont
   désactivés, les équipes touchées sortent du classement jusqu'à ce
   qu'elles soient complétées. Supprimer la ligue fait le même ménage.
2. **Splits › Importer une ligue** : choisis une ou plusieurs ligues et
   années (case « compétitions internationales » pour MSI, Worlds, First
   Stand). Tout le reste suit : tournois, splits, parties, cartes, boosters.

Une ligue importée comprend ses playoffs, play-ins et « Road to MSI ».
Les qualifications (montée, qualifs régionales) sont ignorées, sauf si la
case est cochée. MSI et Worlds sont des ligues à part sur Leaguepedia.

Une fois les splits créés, on peut aussi relancer l'import de quelques
splits : **Splits**, cocher les splits, puis **Importer depuis
Leaguepedia**.

- L'import part au worker.
- Un rapport s'affiche à la fin, avec une section par split, ses avertissements
  (joueurs introuvables…) et le nombre de cartes créées.

Noms courts des ligues : ceux de Leaguepedia (LEC, LCK, LPL, LFL, LTA North,
WCS pour les Worlds, MSI…).

| Commande | Rôle |
|---|---|
| `lp_tournaments --league LEC --year 2025 [--name-like Spring] [--save]` | Liste ou enregistre les tournois, et crée les splits |
| `lp_import --league LEC --year 2025` ou `--split <slug>` | Import complet (`--no-images` pour aller plus vite) |
| `recompute_cards [--split <slug>]` | Recalcule stats, notes et raretés sans réseau |
| `init_boosters` | Raretés par défaut et boosters types |

## La gestion (django-generic)

Les écrans sont déclarés dans `esport/resources.py` et `cards/resources.py`.
Aucune vue n'est écrite à la main : le framework génère listes, filtres,
exports, résumés, formulaires et API.

- **Données Leaguepedia**
  - *Ligues*, *Splits*, *Tournois*, *Équipes*.
  - *Joueurs* : vue « À vérifier » pour ceux que l'import n'a pas trouvés,
    vue « Sans photo ».
  - *Parties jouées* : le détail partie par partie.
  - *Stats par split* : agrégats, score et percentile.
  - Un tournoi se rattache à un autre split directement dans la liste,
    avec le bouton *Modifier*.
- **Jeu**
  - *Raretés* : taux, bonus, fusion et échange modifiables dans la liste.
  - *Cartes* :
    - activation modifiable dans la liste ou en masse ;
    - page **Aperçu** qui montre la carte comme les joueurs la voient ;
    - graphiques par rôle et par période.
  - *Boosters* : visuel, rythme, contenu et poids de rareté propres à un booster.
- **Joueurs du site** : *Collections* et *Ouvertures de boosters*, avec un
  graphique par jour.
- **Tableau de bord** : le nombre de joueurs à vérifier et leurs fiches.

## Périodes

Une carte = un joueur × une période :

- **Ligue locale** : toute l'année ensemble (« LCK 2026 » = LCK Cup, Rounds
  1-2, Rounds 3-4 et playoffs), pour des notes sur assez de parties.
- **Compétition internationale** (région « International » sur Leaguepedia :
  MSI, Worlds, First Stand…) : chaque événement à part. Ces cartes ont moins
  de parties et leurs boosters automatiques tombent moins souvent.

Les nouveaux imports sont regroupés d'office. Pour les données importées
avant : **Splits › Regrouper les phases par année** (ou
`python manage.py recompute_cards`, qui commence par là). Les exemplaires
possédés passent sur la carte de l'année, à la même rareté ; les équipes et
les ouvertures suivent.

## Note d'un joueur

La note s'inspire des notes par partie des sites de stats pros (DPM.lol ne
publie pas sa formule) :

1. **Chaque partie** reçoit une note de 0 à 100, où 50 est la partie moyenne
   d'un pro du **même rôle**, sur toutes les parties importées. Métriques :
   KDA, participation aux kills, morts/min, dégâts/min, part des dégâts,
   dégâts par gold, CS/min, gold/min, vision/min, victoire. Les poids par
   rôle sont dans `esport/scoring.py` (`ROLE_WEIGHTS`).
2. **La note sur la période** est la moyenne de ses parties, tirée vers 50
   quand il a peu joué (`GAME_RATING_PRIOR_GAMES`, 3 par défaut).
3. **La note de la carte** (50 à 99) en découle ; un pro moyen a 74.
4. **La note sur un champion** : ses parties dessus pendant la période (la
   forme du moment) comptent pleinement, ses parties d'avant (historique)
   comptent à moitié (`HISTORY_WEIGHT`), le tout tiré vers sa note de la
   période quand il y en a peu. Deux parties moyennes cette saison mais
   cinq très bonnes avant : la note reste bonne. Un champion jamais joué en
   compétition vaut sa note moins 10 (`UNPLAYED_PENALTY`).

## Raretés, fusions, échanges

Chaque joueur existe à toutes les raretés : la rareté est celle de
l'exemplaire, tirée à l'ouverture selon les taux, puis le joueur au hasard.

| Rareté | Taux de tirage | Bonus en équipe | Doublons |
|---|---|---|---|
| Commune | 62 % | 0 % | 2 → 1 rare |
| Rare | 27 % | 5 % | 2 → 1 épique |
| Épique | 9 % | 10 % | 2 → 1 légendaire |
| Légendaire | 2 % | 20 % | 1 doublon → le joueur de son choix, en rare |

Tout se règle dans **Jeu › Raretés** (directement dans la liste) : taux,
bonus, exemplaires pour fusionner, doublons pour un échange et rareté reçue.
Fusions et échanges se font depuis la fiche d'une carte. Un booster peut
avoir ses propres taux (onglet du booster).

## Boosters

- **Rythme.** Chaque booster se recharge à son rythme : un toutes les N
  heures (1 par défaut), jusqu'à un stock maximum (24) ; au-delà, ils sont
  perdus. Un nouveau joueur en reçoit 3. Tout se règle par booster dans
  **Jeu › Boosters** ; les boosters internationaux automatiques partent sur
  6 heures et 6 au maximum.
- **Boosters types.** Un booster par région et par année (« Corée 2026 »),
  un booster **Esports World Cup** par année, un booster **Riot
  International** (First Stand, MSI, Worlds) par année, et « Tous les
  splits ». Ils sont créés tout seuls après chaque import et à chaque
  démarrage du site (`python manage.py init_boosters`), ou à la demande :
  **Jeu › Boosters › Créer les boosters types**. Les réglages d'un booster
  existant (rythme, visuel, taux) sont gardés.
- **Offrir des boosters.** **Jeu › Boosters › Offrir des boosters** : un
  booster, un nombre, des joueurs (ou tous). Ils s'ajoutent à la réserve
  sans plafond ni recharge ; le joueur voit le cadeau à sa prochaine
  visite. Historique : **Joueurs du site › Cadeaux de boosters**.
- **Boosters à la main.** Dans **Jeu › Boosters**, en choisissant des
  ligues, des années (« 2025, 2026 ») et des périodes.
- **Visuel.** Chaque booster peut recevoir une image (PNG, JPEG, WebP ou
  GIF), dans **Jeu › Boosters**, section Affichage. Elle habille le paquet
  sur l'accueil et à l'ouverture ; format conseillé 5 × 7 (500 × 700 px).
  Sans image, le paquet prend un dégradé de sa couleur.

## Champions, équipes, classement

- **Champions.** **Champions › Synchroniser les champions** récupère sur
  Data Dragon (Riot) tous les champions : titre, classes, profil attaque /
  défense / magie, icône, illustration et splash (téléchargés dans
  `game_media/champions/`). L'import le fait seul pour les nouveaux.
- **Équipes.** 5 joueurs de sa collection, un par poste, et 5 champions
  différents par joueur. La composition n réunit le n-ième champion de
  chaque joueur (un champion ne sert qu'une fois par composition).
  - Poste = (50 % note du joueur + 50 % note sur le champion) × bonus de
    rareté de son meilleur exemplaire.
  - Cohérence d'une composition (sur 100) : dégâts mixtes physiques /
    magiques, ligne de front, sources de dégâts, contrôle et protection,
    classes variées, plus un bonus pour les duos déjà joués ensemble en
    compétition.
  - Composition = 75 % moyenne des postes + 25 % cohérence
    (`COHERENCE_WEIGHT`).
  - Équipe = moyenne des 5 compositions. Le constructeur montre tout en
    direct.
- **Classement.** Une équipe complète (5 joueurs, 25 champions) est
  classée ; la page **Classement** retient la meilleure de chaque joueur.
  Les scores se recalculent après un import, une fusion ou une ouverture.

## Héberger sur un Raspberry Pi

Tout tourne dans Docker sur le Pi : Django (Daphne), Celery, Postgres,
Redis et **Caddy**, qui fait le HTTPS (Let's Encrypt) et filtre les accès.

| Depuis | Ce qui répond |
|---|---|
| Internet, `https://<domaine>/` | le jeu seulement ; `/gestion/`, son API et ses WebSockets répondent 403 |
| Réseau local, `https://<IP du Pi>:8443/` | tout, gestion comprise (certificat local : le navigateur avertit la première fois) |
| Réseau local, SSH | par clé seulement, utilisateur `pi` seulement |
| Internet, autre chose | rien : pare-feu (ufw, et Docker filtré lui aussi), et la Freebox ne redirige que 80 et 443 |

### Pré-requis

- Un Pi 4 ou 5 (2 Go de mémoire au moins) sous **Raspberry Pi OS 64 bits**
  (Lite suffit), SSH activé (Raspberry Pi Imager le propose).
- Le projet sur GitHub. Dépôt privé : le script fournit une clé de
  déploiement à ajouter dans GitHub (lecture seule).

### Installation

1. Dans Freebox OS › **Périphériques réseau**, repère l'IP du Pi. Si
   « raspberry » n'est pas reconnu par son nom sur le réseau, l'IP suffit.
2. Sur le PC : `deploy\raspberry\installer-depuis-windows.bat`. Il crée ta
   clé SSH, la pose sur le Pi (tu tapes le mot de passe du Pi une dernière
   fois), copie `install.sh` sur le Pi et le lance. Le script :
   - propose de changer le mot de passe de `pi` (à faire s'il vaut encore
     « raspberry ») ;
   - met le système à jour et active les mises à jour de sécurité
     automatiques ;
   - installe Docker (dépôt officiel) ;
   - coupe la connexion SSH par mot de passe et l'accès root, limite SSH au
     réseau local, ajoute fail2ban ;
   - ferme tout sauf 80 et 443 (et 22, 8443 pour le réseau local) ;
   - clone le projet dans `/opt/wiki-lol-esport`, crée `.env.prod` (secrets
     tirés au hasard), construit et démarre la stack ;
   - sauvegarde la base et les images chaque nuit dans `backups/` (14 jours).
3. Le compte administrateur, sur le Pi :

   ```bash
   cd /opt/wiki-lol-esport
   sudo docker compose -f compose.prod.yaml --env-file .env.prod exec web python manage.py createsuperuser
   ```

   Ou bien reprends les données du PC (comptes, collections, imports,
   images) : `deploy\raspberry\exporter-donnees.bat`, la stack de dev
   allumée.

### La Freebox

Dans Freebox OS (http://mafreebox.freebox.fr) :

1. **IPv4 full-stack** : sur l'espace abonné Free (Ma Freebox › Demander une
   adresse IP fixe V4 full-stack). Sans elle, l'IPv4 est partagée et les
   ports 80 / 443 ne sont pas à toi.
2. **Bail DHCP statique** : Paramètres › DHCP › Baux statiques : une IP fixe
   pour le Pi.
3. **Redirections de ports** : Paramètres › Gestion des ports : TCP **80 → 80**
   et TCP **443 → 443** vers l'IP du Pi. Rien d'autre (surtout pas 22 ni 8443).
4. **Accès à distance de Freebox OS** : s'il utilise le port 80 ou 443,
   change son port (Paramètres › Accès à distance).
5. **Nom de domaine** : Paramètres › Nom de domaine pour un nom gratuit
   `xxx.freeboxos.fr`, ou n'importe quel domaine (DuckDNS, OVH…) pointé vers
   l'IP publique. C'est le `SITE_DOMAIN` demandé par le script. Le
   certificat HTTPS est obtenu tout seul au premier accès.

Vérifier depuis la 4G (hors Wi-Fi) : `https://<domaine>/` montre le jeu,
`https://<domaine>/gestion/` répond « Accès réservé au réseau local ».

### Déploiement continu (tags)

Une nouvelle version se publie par un tag `vX.Y.Z` :

```bash
git tag v1.2.0
git push origin v1.2.0
```

1. **GitHub Actions** (`.github/workflows/ci.yml`) lance les tests à
   chaque push ; sur un tag, il construit aussi l'image de production pour
   le Pi (arm64) et crée une release GitHub.
2. **Le Pi** regarde toutes les 5 minutes s'il y a un nouveau tag
   (`deploy/raspberry/deploiement-auto.sh`, timer systemd
   `leaguegacha-deploiement`). Il ne déploie que si le job `tests` de ce tag
   est vert : sauvegarde, passage au tag, reconstruction, relance (les
   migrations et les boosters types passent au démarrage), puis il vérifie
   que le site répond. Sinon, il revient à la version d'avant.

Le Pi va chercher les nouveautés lui-même : aucun port de plus à ouvrir,
aucun secret dans GitHub. Sur un Pi installé avant cette procédure, une
fois : `git pull && bash deploy/raspberry/activer-deploiement-auto.sh`.

Suivre les déploiements : `journalctl -u leaguegacha-deploiement -f`.

### Au quotidien, sur le Pi (`cd /opt/wiki-lol-esport`)

| Commande | Rôle |
|---|---|
| `./deploy/raspberry/mise-a-jour.sh` | Déploie tout de suite le dernier tag aux tests verts (`v1.2.0` : un tag précis ; `--main` : la branche main) |
| `./deploy/raspberry/sauvegarde.sh` | Sauvegarde tout de suite (base + images) |
| `./deploy/raspberry/importer-donnees.sh base.dump fichiers.tar.gz` | Restaure une sauvegarde |
| `sudo docker compose -f compose.prod.yaml --env-file .env.prod logs -f caddy web` | Les journaux |
| `sudo ufw status` / `sudo fail2ban-client status sshd` | Pare-feu, adresses bannies |

## Tests

```bash
docker compose run --rm web pytest
```

La suite compte environ 600 tests :

- **Logique pure** : client Leaguepedia, agrégation, tirage, notes sur champion, cohérence.
- **Import complet** : un faux Leaguepedia, des tournois jusqu'aux cartes.
- **Jeu** : boosters (réserve), fusions, échanges, équipes, regroupement, pages.
- **Opérations de la gestion.**
- **Balayage `PageSweep` de django-generic** : chaque page et chaque endpoint
  de la gestion, ouverts en superutilisateur, sans permission et déconnecté.
  Il vérifie aussi que les listes ne font pas une requête SQL par ligne.

## Organisation

```
config/         settings (base, dev, prod), urls, asgi, celery
esport/         données Leaguepedia : modèles, client, import, stats, gestion, opérations
cards/          le jeu : raretés, cartes, boosters, tirage, vues, gestion
templates/      pages du jeu (+ cards/gestion/ : aperçu dans la gestion)
static/         CSS du jeu (style.css) et de la carte (cards.css), JS (ouverture, constructeur d'équipe, comptes à rebours)
tests/          pytest
docker/         Dockerfile (dev) et Dockerfile.prod
deploy/         Caddyfile, scripts du Raspberry Pi (installation, mise à jour, sauvegardes)
django-generic/ le framework (sous-module git)
```

## Points d'attention

- **Leaguepedia limite le débit.** Garde `LEAGUEPEDIA_DELAY` à 1,5 s ou plus,
  et mets un contact dans `LEAGUEPEDIA_USER_AGENT`.
- **Photos.** Les photos et logos sont dans `game_media/`, servis en direct au
  jeu. Les fichiers privés du framework restent dans `media/`. Les photos
  appartiennent en général à Riot ou aux ligues : un usage perso convient,
  mais il faut vérifier les droits avant de mettre le site en public.
- **Mettre à jour le framework.** Lancer
  `git submodule update --remote django-generic`, puis
  `docker compose build` et `docker compose up -d`.
