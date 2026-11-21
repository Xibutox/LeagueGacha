from django.core.management.base import BaseCommand, CommandError

from cards.services import download_card_images, generate_cards
from esport.importer import LeaguepediaImporter
from esport.models import Split


class Command(BaseCommand):
    help = (
        "Importe depuis Leaguepedia les parties, joueurs et photos des splits demandés, "
        "calcule les stats et génère les cartes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--split", action="append", help="Slug du split (répétable)")
        parser.add_argument("--league", help="Nom court de la ligue (tous ses splits)")
        parser.add_argument("--year", type=int, help="Année (avec --league)")
        parser.add_argument("--no-images", action="store_true", help="Ne pas télécharger les photos")

    def handle(self, *args, **opts):
        qs = Split.objects.all()
        if opts["split"]:
            qs = qs.filter(slug__in=opts["split"])
        elif opts["league"]:
            qs = qs.filter(league__short_name__iexact=opts["league"])
            if opts["year"]:
                qs = qs.filter(year=opts["year"])
        else:
            raise CommandError("Précise --split <slug> ou --league <LEC> [--year 2025].")
        if not qs.exists():
            raise CommandError("Aucun split trouvé. Lance d'abord : lp_tournaments --league … --year … --save")

        importer = LeaguepediaImporter(log=self.stdout.write)
        for split in qs:
            importer.import_split(split, with_images=not opts["no_images"])
            created, updated = generate_cards(split)
            self.stdout.write(f"  cartes : {created} créées, {updated} mises à jour")
            if not opts["no_images"]:
                n = download_card_images(split, importer)
                self.stdout.write(f"  {n} photo(s) téléchargée(s)")
        self.stdout.write(self.style.SUCCESS("Import terminé."))
