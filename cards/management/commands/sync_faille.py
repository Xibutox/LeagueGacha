from django.core.management.base import BaseCommand

from cards import faille


class Command(BaseCommand):
    help = (
        "La Faille : importe les objets de Data Dragon (en français) et télécharge les images "
        "(objets, sbires et monstres du LoL Wiki, augments). --si-besoin : seulement si rien n'est "
        "encore importé (lancé à chaque démarrage du site)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--si-besoin", action="store_true")
        parser.add_argument("--sans-images", action="store_true")
        parser.add_argument("--regles-sim", metavar="FICHIER",
                            help="Écrit les règles et le catalogue pour le simulateur (tools/faille-sim.js), sans rien importer.")

    def handle(self, *args, **opts):
        if opts["regles_sim"]:
            self._dump(opts["regles_sim"])
            return
        if opts["si_besoin"] and faille.is_ready():
            return
        try:
            result = faille.sync(images=not opts["sans_images"], log=self.stdout.write)
        except Exception as exc:  # noqa: BLE001 - le site démarre quand même
            self.stderr.write(f"La Faille : import impossible ({exc}). Relancer : python manage.py sync_faille")
            return
        self.stdout.write(f"La Faille : {result['items']} objets (Data Dragon {result['version']}), {result['images']} image(s).")

    def _dump(self, path):
        import json

        from cards import faille_rules as R

        seed = R.week_seed("2026-11-23")
        rules = {
            "enemies": R.sequence(seed, 0, 900), "enemiesElder": R.sequence(seed, 0, 900, elder=True),
            "catalog": faille.catalog(), "augments": R.AUGMENTS, "drakeBuffs": R.DRAKE_BUFFS,
            "enemyBuffs": {k: v["buff"] for k, v in R.ENEMIES.items() if v.get("buff")},
        }
        with open(path, "w") as fh:
            json.dump(rules, fh)
        self.stdout.write(f"Règles écrites dans {path} ({len(rules['catalog'])} objets).")
