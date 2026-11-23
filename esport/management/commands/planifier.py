"""Crée le planning des mises à jour automatiques (esport/schedules.py).

    python manage.py planifier            # ce qui manque seulement
    python manage.py planifier --reset    # remet le planning par défaut
"""
from django.core.management.base import BaseCommand

from esport import schedules


class Command(BaseCommand):
    help = "Crée les tâches planifiées (imports Leaguepedia, Fantasy) dans django-celery-beat."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Remettre les horaires par défaut.")

    def handle(self, *args, reset=False, **options):
        if not schedules.available():
            self.stdout.write(self.style.WARNING("django-celery-beat n'est pas installé : rien à planifier."))
            return
        done = schedules.ensure_schedules(force=reset)
        self.stdout.write(self.style.SUCCESS(
            f"Planning : {', '.join(done)}" if done else "Planning déjà en place."))
