"""La tâche planifiée de la Fantasy (cards/fantasy.py) : compétitions,
verrouillage, matchs simulés, points, comptage et récompenses.

    python manage.py fantasy_tick            # une fois
    python manage.py fantasy_tick --boucle   # toutes les 3 h (service du compose)
"""
import time

from django.core.management.base import BaseCommand

from cards import fantasy


class Command(BaseCommand):
    help = "Fait avancer la Fantasy League : semaines, points, récompenses."

    def add_arguments(self, parser):
        parser.add_argument("--boucle", action="store_true", help="Recommence toutes les --minutes.")
        parser.add_argument("--minutes", type=int, default=180)

    def handle(self, *args, boucle=False, minutes=180, **options):
        while True:
            try:
                stats = fantasy.tick(log=lambda m: self.stdout.write(m))
                self.stdout.write(self.style.SUCCESS(f"Fantasy à jour : {stats or 'rien de neuf'}"))
            except Exception as exc:  # la boucle ne doit pas s'arrêter sur une erreur passagère
                if not boucle:
                    raise
                self.stderr.write(f"Fantasy : erreur {exc!r}, nouvel essai dans {minutes} min")
            if not boucle:
                return
            time.sleep(minutes * 60)
