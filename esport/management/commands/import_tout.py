from django.core.management.base import BaseCommand

from esport.tasks import import_everything


class Command(BaseCommand):
    help = (
        "Importe toutes les années disponibles de la LEC, la LCK, la LPL, la LCS, des First "
        "Stand, MSI, Worlds et de l'Esports World Cup (long : plusieurs heures la première "
        "fois). Relançable : les splits terminés et déjà importés sont sautés."
    )

    def add_arguments(self, parser):
        parser.add_argument("--sans-photos", action="store_true", help="Ne pas télécharger photos et logos.")
        parser.add_argument("--tout-reimporter", action="store_true",
                            help="Réimporter aussi les splits terminés déjà importés.")
        parser.add_argument("--fond", action="store_true",
                            help="Confier l'import au worker et rendre la main tout de suite (suivi dans la gestion).")

    def handle(self, *args, **opts):
        run = import_everything.start(
            None, background=opts["fond"], images=not opts["sans_photos"], refresh=opts["tout_reimporter"]
        )
        if opts["fond"]:
            self.stdout.write("Import confié au worker : suis-le dans la gestion (Exécutions).")
            return
        self.stdout.write(f"{run.get_status_display()} : {run.summary or run.error}")
