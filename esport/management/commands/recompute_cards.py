from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from cards.services import ensure_boosters, generate_cards, regroup_splits
from esport.importer import LeaguepediaImporter
from esport.models import Split
from esport.scoring import SCORING_VERSION


def version_file() -> Path:
    return Path(settings.MEDIA_ROOT) / ".version-notes"


class Command(BaseCommand):
    help = (
        "Regroupe les phases des ligues locales par année, puis recalcule stats, notes, "
        "champions et équipes à partir des données locales (aucun appel réseau). "
        "--si-besoin : seulement si le calcul des notes a changé depuis la dernière fois "
        "(lancé à chaque démarrage du site)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--split", action="append", help="Slug du split (par défaut : tous)")
        parser.add_argument(
            "--si-besoin", action="store_true",
            help="Ne rien faire si les notes ont déjà été calculées avec la version actuelle.",
        )

    def handle(self, *args, **opts):
        marker = version_file()
        if opts["si_besoin"]:
            done = marker.read_text().strip() if marker.exists() else ""
            if done == str(SCORING_VERSION):
                return
            if not Split.objects.exists():
                self._mark(marker)
                return
            self.stdout.write(f"Calcul des notes passé en version {SCORING_VERSION} : recalcul de tout.")

        if not opts["split"]:
            for name in regroup_splits(log=self.stdout.write):
                self.stdout.write(f"{name} : phases regroupées")
        splits = list(Split.objects.all())
        if opts["split"]:
            splits = [s for s in splits if s.slug in opts["split"]]
        # D'abord toutes les notes de parties, puis les cartes : la note d'un
        # champion tient compte de l'historique, pris dans les autres périodes.
        counts = {split.pk: LeaguepediaImporter.compute_split_stats(split) for split in splits}
        for split in splits:
            created, updated = generate_cards(split)
            self.stdout.write(f"{split} : {counts[split.pk]} stats, {created} cartes créées, {updated} mises à jour")
        for name in ensure_boosters():
            self.stdout.write(f"Booster « {name} » créé")
        if not opts["split"]:
            self._mark(marker)

    def _mark(self, marker: Path) -> None:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(SCORING_VERSION))
        except OSError as exc:  # pragma: no cover - dossier en lecture seule
            self.stderr.write(f"Version des notes non enregistrée : {exc}")
