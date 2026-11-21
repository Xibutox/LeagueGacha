from django.core.management.base import BaseCommand

from esport.importer import LeaguepediaImporter


class Command(BaseCommand):
    help = (
        "Liste les tournois Leaguepedia d'une ligue pour une année. "
        "Avec --save, les enregistre et crée les splits correspondants."
    )

    def add_arguments(self, parser):
        parser.add_argument("--league", required=True, help="Nom court de la ligue : LEC, LCK, LPL, LFL, WCS…")
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--name-like", help="Filtre sur le nom du tournoi (ex. « Spring »)")
        parser.add_argument("--save", action="store_true", help="Enregistrer en base")
        parser.add_argument(
            "--include-qualifiers", action="store_true", help="Rattacher aussi les qualifications à un split"
        )

    def handle(self, *args, **opts):
        importer = LeaguepediaImporter(log=self.stdout.write)
        rows = importer.discover_tournaments(opts["league"], opts["year"], opts.get("name_like"))
        if not rows:
            self.stdout.write(self.style.WARNING("Aucun tournoi trouvé. Vérifie le nom court de la ligue."))
            return
        for row in rows:
            flags = []
            if row.get("IsPlayoffs") in ("1", "Yes"):
                flags.append("playoffs")
            if row.get("IsQualifier") in ("1", "Yes"):
                flags.append("qualif")
            self.stdout.write(
                f"- {row.get('Name')}  [{row.get('DateStart')} → {row.get('DateEnd')}]"
                f"  split: {importer.split_name_for(row)}  {' '.join(flags)}\n    {row.get('OverviewPage')}"
            )
        if opts["save"]:
            saved = importer.save_tournaments(rows, include_qualifiers=opts["include_qualifiers"])
            self.stdout.write(self.style.SUCCESS(f"{len(saved)} tournoi(s) enregistré(s)."))
        else:
            self.stdout.write("(aperçu seulement : ajoute --save pour enregistrer)")
