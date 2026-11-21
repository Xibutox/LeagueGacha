"""Application Celery : le worker qui fait les imports Leaguepedia en tâche
de fond. Mêmes settings que manage.py par défaut (dev)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
