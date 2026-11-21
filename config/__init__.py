"""Le projet : settings, URLs, points d'entrée des serveurs et Celery.

L'application Celery est importée ici (le schéma habituel de Celery avec
Django) : sans cette ligne, une opération lancée depuis une page ne trouve
pas de broker et tourne dans la requête au lieu du worker.
"""

import importlib.util

if importlib.util.find_spec("celery") is not None:
    from .celery import app as celery_app

    __all__ = ["celery_app"]
