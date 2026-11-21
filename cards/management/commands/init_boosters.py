from django.core.management.base import BaseCommand

from cards.services import ensure_boosters, ensure_default_rarities


class Command(BaseCommand):
    help = (
        "Crée les raretés par défaut et les boosters types : « Tous les splits », un booster "
        "par région et par année, l'Esports World Cup par année et Riot International "
        "(First Stand, MSI, Worlds) par année. Relançable sans risque : les réglages des "
        "boosters existants sont gardés. Lancé à chaque démarrage du site."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--garder-anciens", action="store_true",
            help="Ne pas désactiver les boosters automatiques d'un ancien découpage.",
        )

    def handle(self, *args, **opts):
        n = ensure_default_rarities()
        if n:
            self.stdout.write(f"{n} rareté(s) créée(s).")
        created = ensure_boosters(deactivate_obsolete=not opts["garder_anciens"])
        for name in created:
            self.stdout.write(f"Booster « {name} » créé.")
        self.stdout.write(self.style.SUCCESS(f"Boosters types prêts ({len(created)} nouveau(x))."))
