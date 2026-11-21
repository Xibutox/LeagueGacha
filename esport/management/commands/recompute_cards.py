from django.core.management.base import BaseCommand

from cards.services import ensure_boosters, generate_cards, regroup_splits
from esport.importer import LeaguepediaImporter
from esport.models import Split


class Command(BaseCommand):
    help = (
        "Regroupe les phases des ligues locales par année, puis recalcule stats, notes, "
        "champions et équipes à partir des données locales (aucun appel réseau)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--split", action="append", help="Slug du split (par défaut : tous)")

    def handle(self, *args, **opts):
        if not opts["split"]:
            for name in regroup_splits(log=self.stdout.write):
                self.stdout.write(f"{name} : phases regroupées")
        qs = Split.objects.all()
        if opts["split"]:
            qs = qs.filter(slug__in=opts["split"])
        for split in qs:
            n = LeaguepediaImporter.compute_split_stats(split)
            created, updated = generate_cards(split)
            self.stdout.write(f"{split} : {n} stats, {created} cartes créées, {updated} mises à jour")
        for name in ensure_boosters():
            self.stdout.write(f"Booster « {name} » créé")
