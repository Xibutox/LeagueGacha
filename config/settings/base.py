"""Ce qu'est le projet, quelle que soit la machine.

Le câblage de django-generic suit ``example_project/settings/base.py`` du
framework (voir django-generic/docs/installation.md). Ce qui change entre
développement et production est dans ``dev.py`` et ``prod.py``.
"""

import importlib.util
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import django
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

#: La racine du projet : settings/ est deux niveaux en dessous.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# -- Lecture de l'environnement ------------------------------------------


def env(name, default=""):
    """Une variable d'environnement, ou ``default`` si absente ou vide."""
    return os.environ.get(name) or default


def env_required(name):
    """Une variable sans laquelle la production refuse de démarrer."""
    value = os.environ.get(name)

    if not value:
        raise ImproperlyConfigured(
            f"{name} n'est pas défini : la production le lit dans "
            f"l'environnement (voir .env.example)."
        )

    return value


def env_bool(name, default=False):
    value = os.environ.get(name)

    if not value:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default=0):
    value = os.environ.get(name)

    return int(value) if value else default


def env_float(name, default=0.0):
    value = os.environ.get(name)

    return float(value) if value else default


def env_list(name, default=()):
    value = os.environ.get(name)

    if not value:
        return list(default)

    return [item.strip() for item in value.split(",") if item.strip()]


def database_from_url(url):
    """``DATABASES["default"]`` depuis une URL postgres:// ou sqlite:///."""
    parsed = urlparse(url)

    if parsed.scheme in {"postgres", "postgresql", "pgsql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }

    if parsed.scheme == "sqlite":
        path = unquote(parsed.path)

        if path.startswith("//"):
            name = Path(path[1:])
        else:
            name = BASE_DIR / path.lstrip("/")

        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}

    raise ImproperlyConfigured(
        f"DATABASE_URL : '{parsed.scheme}' n'est pas une base connue. "
        f"Utiliser postgres://... ou sqlite:///..."
    )


def redis_backends(url):
    """La couche de canaux (événements temps réel) et le cache, sur Redis."""
    return (
        {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [{"address": url, "socket_timeout": 15}]},
            }
        },
        {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": url,
            }
        },
    )


def mail_settings(backend, **options):
    """Les settings d'envoi de mail, pour Django 5.2 comme pour 6.1+."""
    if django.VERSION >= (6, 1):
        mailer = {"BACKEND": backend}

        if options:
            mailer["OPTIONS"] = options

        return {"MAILERS": {"default": mailer}}

    legacy = {
        "host": "EMAIL_HOST",
        "port": "EMAIL_PORT",
        "username": "EMAIL_HOST_USER",
        "password": "EMAIL_HOST_PASSWORD",
        "use_tls": "EMAIL_USE_TLS",
        "use_ssl": "EMAIL_USE_SSL",
    }

    return {
        "EMAIL_BACKEND": backend,
        **{legacy[name]: value for name, value in options.items()},
    }


def installed(module):
    return importlib.util.find_spec(module) is not None


# -- Applications ------------------------------------------------------

#: Temps réel (extra « events » du framework) : présent dans l'image
#: Docker, facultatif ailleurs.
EVENTS = installed("channels") and installed("daphne")

INSTALLED_APPS = [
    # En premier : runserver parle alors ASGI (WebSockets du framework).
    *(["daphne"] if EVENTS else []),
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # --- le framework ---
    *(["channels"] if EVENTS else []),
    "rest_framework",
    "generic",
    # --- le projet ---
    "esport",
    "cards",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Ceux du framework, après l'authentification : ils lisent l'utilisateur.
    "generic.middleware.UserLanguageMiddleware",
    "generic.middleware.CurrentUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                # Requis par le framework : chaque page dessine son cadre
                # depuis la requête.
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "cards.context_processors.game",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -- Langue --------------------------------------------------------------

LANGUAGE_CODE = "fr"
# Une seule langue : pas de menu de langue dans le framework.
LANGUAGES = [("fr", _("French"))]
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# -- Fichiers statiques ----------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = Path(env("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]

# -- Fichiers ----------------------------------------------------------------
#
# MEDIA_ROOT : fichiers privés du framework, servis par les endpoints de
# chaque enregistrement (jamais en direct).
# GAME_MEDIA_* : photos des joueurs et logos des équipes, publics, servis
# en direct au jeu (par runserver en dev, par le proxy en production).

MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT") or BASE_DIR / "media")
GAME_MEDIA_ROOT = Path(env("GAME_MEDIA_ROOT") or BASE_DIR / "game_media")
GAME_MEDIA_URL = "/game-media/"

# -- Authentification ----------------------------------------------------
#
# Le jeu a sa propre page de connexion ; la gestion (django-generic) a la
# sienne, sous /gestion/login/.

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "cards:home"
LOGOUT_REDIRECT_URL = "cards:home"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation."
        "NumericPasswordValidator"
    },
]

# -- REST framework (utilisé par les pages du framework) ---------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# -- Logs ------------------------------------------------------------------

from generic.logs import QUIET, admins, logging_config  # noqa: E402

ADMINS = admins(env_list("DJANGO_ADMINS"))
LOGGING = logging_config(
    level=env("DJANGO_LOG_LEVEL", "INFO"),
    file=env("DJANGO_LOG_FILE") or None,
    # « channels over capacity » : sans intérêt, et des centaines pendant un import.
    quiet=(*QUIET, "channels_redis.core"),
)
EMAIL_SUBJECT_PREFIX = "[LeagueGacha] "
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# -- Le framework ------------------------------------------------------------

GENERIC = {
    "SITE_TITLE": "LeagueGacha — gestion",
    "SITE_ICON": "playing_cards",
    # Doré, comme le jeu.
    "THEME": {"--ui-hue": "75"},
    "TABLE_PAGE_SIZE": 25,
    # Les pages ne s'abonnent au temps réel que si Channels est là.
    **({} if EVENTS else {"EVENTS_WEBSOCKET_URL": None}),
}

# -- Le jeu ------------------------------------------------------------------

#: Le rythme des boosters (un toutes les N heures, stock maximum) se règle
#: par booster, dans la gestion.
#: Les comptes staff ne sont pas limités (pratique pour tester).
GAME_STAFF_UNLIMITED = env_bool("GAME_STAFF_UNLIMITED", True)
# Durée (jours) des tournois créés automatiquement (chacun reste modifiable dans la gestion).
GAME_TOURNAMENT_DAYS = env_int("GAME_TOURNAMENT_DAYS", 7)
#: Dans la note d'un split, le nombre de parties « moyennes de la ligue »
#: ajoutées à celles du joueur : moins il a joué, plus sa note reste proche
#: de la moyenne de sa ligue.
GAME_RATING_LEAGUE_GAMES = env_int("GAME_RATING_LEAGUE_GAMES", 8)

# -- Leaguepedia -------------------------------------------------------------

#: La version déployée (un tag vX.Y.Z), affichée en bas du site.
APP_VERSION = env("APP_VERSION", "")

#: Après un import, compléter les nouveaux champions par Data Dragon (Riot).
DDRAGON_AUTO_SYNC = env_bool("DDRAGON_AUTO_SYNC", True)

LEAGUEPEDIA_USER_AGENT = env(
    "LEAGUEPEDIA_USER_AGENT", "LeagueGacha/0.2 (projet perso)"
)
#: Pause (secondes) entre deux requêtes, pour ne pas être limité.
LEAGUEPEDIA_DELAY = env_float("LEAGUEPEDIA_DELAY", 1.5)
#: Largeur des photos téléchargées (Fandom génère la miniature).
LEAGUEPEDIA_IMAGE_WIDTH = env_int("LEAGUEPEDIA_IMAGE_WIDTH", 400)
