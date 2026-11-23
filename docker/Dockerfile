# syntax=docker/dockerfile:1
#
# Image de développement : le framework installé depuis le sous-module
# django-generic/, les dépendances du projet, runserver.
# compose.yaml monte le dossier du projet sur /app : le code (projet et
# framework) se recharge sans reconstruire l'image.
#
# À reconstruire (docker compose build) après une mise à jour du
# sous-module qui change ses dépendances, ou de requirements.txt.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Le code monté passe avant ce que pip a installé : le projet, puis
    # le framework tel qu'il est dans le sous-module.
    PYTHONPATH=/app:/app/django-generic

WORKDIR /app

# Le framework, à partir de ses seules métadonnées et de son paquet : une
# modification du projet réutilise cette couche.
COPY django-generic/pyproject.toml django-generic/README.md django-generic/LICENSE ./django-generic/
COPY django-generic/generic/ ./django-generic/generic/
# Les versions testées par la CI du framework (Django, DRF, Celery...).
COPY django-generic/requirements/constraints.txt ./django-generic/requirements/constraints.txt
ENV PIP_CONSTRAINT=/app/django-generic/requirements/constraints.txt

COPY requirements.txt ./
RUN pip install "./django-generic[export,events,tasks,beat,postgres]" -r requirements.txt

COPY . .

EXPOSE 8000

# runserver (ASGI grâce à daphne) : rechargement auto + fichiers statiques.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
