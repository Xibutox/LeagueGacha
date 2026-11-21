from django.core.management.base import BaseCommand

from cards.services import ensure_boosters, ensure_default_rarities


class Command(BaseCommand):
    help = "Crée les raretés par défaut et un booster par split (+ un booster « Tous les splits »)."

    def handle(self, *args, **opts):
        n = ensure_default_rarities()
        self.stdout.write(f"{n} rareté(s) créée(s).")
        for name in ensure_boosters():
            self.stdout.write(f"Booster « {name} » créé.")
        self.stdout.write(self.style.SUCCESS("Jeu prêt."))
