"""Production : tout depuis l'environnement, Postgres, Redis, HTTPS.

Utilisé par compose.prod.yaml (Raspberry Pi), derrière Caddy. Il refuse
de démarrer sans ses secrets (.env.prod, créé par deploy/raspberry/install.sh).
"""

from .base import *  # noqa: F401,F403
from .base import (
    database_from_url,
    env,
    env_bool,
    env_int,
    env_list,
    env_required,
    mail_settings,
    redis_backends,
)

DEBUG = False
SECRET_KEY = env_required("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS") or [
    env_required("DJANGO_ALLOWED_HOSTS")
]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

DATABASES = {"default": database_from_url(env_required("DATABASE_URL"))}
DATABASES["default"]["CONN_MAX_AGE"] = env_int("DJANGO_CONN_MAX_AGE", 60)

REDIS_URL = env_required("REDIS_URL")
CHANNEL_LAYERS, CACHES = redis_backends(REDIS_URL)
# Les sessions lues dans Redis (écrites aussi en base) : une requête SQL de
# moins par page.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage."
        "ManifestStaticFilesStorage"
    },
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0)
SECURE_CONTENT_TYPE_NOSNIFF = True

globals().update(
    mail_settings(
        "django.core.mail.backends.smtp.EmailBackend",
        host=env("DJANGO_EMAIL_HOST", "localhost"),
        port=env_int("DJANGO_EMAIL_PORT", 25),
    )
)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "webmaster@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
