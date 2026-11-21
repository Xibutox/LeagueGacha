"""Développement : DEBUG, et rien d'autre à lancer que Django.

Sans variables, SQLite et une couche de canaux en mémoire. La stack
Docker (compose.yaml) donne DATABASE_URL, REDIS_URL et CELERY_BROKER_URL :
les mêmes settings tournent alors sur Postgres, Redis et Celery.
"""

from .base import *  # noqa: F401,F403
from .base import EVENTS, database_from_url, env, mail_settings, redis_backends

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-seulement-pas-un-secret")
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": database_from_url(env("DATABASE_URL", "sqlite:///db.sqlite3"))
}

REDIS_URL = env("REDIS_URL")

if REDIS_URL and EVENTS:
    CHANNEL_LAYERS, CACHES = redis_backends(REDIS_URL)
elif EVENTS:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

# Sans broker, une tâche tourne dans la requête qui la lance.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "memory://")
CELERY_TASK_ALWAYS_EAGER = not env("CELERY_BROKER_URL")

globals().update(
    mail_settings("django.core.mail.backends.console.EmailBackend")
)
