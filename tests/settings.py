"""Les settings des tests : ceux du développement, sur SQLite en mémoire,
sans services."""

import tempfile
from pathlib import Path

from config.settings.dev import *  # noqa: F401,F403
from config.settings.dev import EVENTS

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
}

if EVENTS:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"

_TMP = Path(tempfile.mkdtemp(prefix="wikilol-tests-"))
MEDIA_ROOT = _TMP / "media"
GAME_MEDIA_ROOT = _TMP / "game_media"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Pas d'appel à Data Dragon pendant les imports des tests.
DDRAGON_AUTO_SYNC = False
GAME_STAFF_UNLIMITED = True
GAME_RATING_LEAGUE_GAMES = 8
