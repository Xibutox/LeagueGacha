"""Les settings du projet, un module par façon de le lancer.

    base.py   ce qu'est le projet, où qu'il tourne
    dev.py    DEBUG, SQLite / mémoire par défaut, Postgres et Redis si donnés
    prod.py   tout depuis l'environnement, HTTPS, Postgres, Redis

``manage.py`` et ``celery.py`` prennent ``dev`` par défaut ; ``asgi.py`` et
``wsgi.py`` prennent ``prod``. ``DJANGO_SETTINGS_MODULE`` choisit sinon.
"""

import os

from django.core.exceptions import ImproperlyConfigured

if os.environ.get("DJANGO_SETTINGS_MODULE") == __name__:
    raise ImproperlyConfigured(
        f"DJANGO_SETTINGS_MODULE={__name__} désigne le paquet : utiliser "
        f"{__name__}.dev ou {__name__}.prod."
    )
