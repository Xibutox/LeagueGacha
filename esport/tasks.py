"""Les opérations lancées depuis la gestion (django-generic).

Une opération est une tâche que démarre un bouton : en tâche de fond, elle
part au worker Celery (ou dans un thread sans broker) et la page reçoit
son rapport à la fin. Voir django-generic/docs/operations.md.
"""

from typing import Any

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from cards.services import download_card_images, ensure_boosters, generate_cards, regroup_splits
from generic.tasks import operation

from .importer import LeaguepediaImporter
from .models import Champion, Split, Team


def _split_url(split):
    return reverse("site:esport_split_detail", args=[split.pk])


def _section_logger(section):
    """Les messages de l'importeur deviennent des lignes du rapport ;
    ceux qui commencent par « ! » sont des avertissements."""

    def log(message):
        text = str(message).strip()

        if text.startswith("!"):
            section.warning(text.lstrip("! "))
        elif text:
            section.info(text)

    return log


@operation(
    name="esport.import_splits",
    label=_("Importer depuis Leaguepedia"),
    description=_(
        "Récupère les parties, les joueurs et les photos des splits "
        "choisis, recalcule les stats et génère les cartes."
    ),
    icon="cloud_download",
    background=True,
)
def import_splits(run: Any, ids: list[Any], images: bool = True) -> str:
    return _import_splits(run, ids, images)


def _import_splits(run: Any, ids: list[Any], images: bool = True) -> str:
    """Le travail des deux opérations d'import : une section par split."""
    importer = LeaguepediaImporter()
    splits = list(Split.objects.filter(pk__in=ids).select_related("league"))
    run.note(f"{len(splits)} split(s) à importer")

    for split in splits:
        with run.report.section(
            split.name, url=_split_url(split), isolated=True
        ) as section:
            importer.log = _section_logger(section)
            result = importer.import_split(split, with_images=images)

            if not result.get("rows"):
                section.warning("Aucune partie trouvée pour ce split.")
                continue

            created, updated = generate_cards(split)
            section.success(
                f"{created} carte(s) créée(s), {updated} mise(s) à jour."
            )

            # Les boosters tout de suite : on peut jouer avec ce split
            # sans attendre la fin de l'import des suivants.
            for name in ensure_boosters():
                section.success(f"Booster « {name} » créé.")

            if images:
                count = download_card_images(split, importer)
                section.info(f"{count} photo(s) téléchargée(s).")

    _sync_new_champions(run)
    return f"{len(splits)} split(s) importé(s)."


def _sync_new_champions(run: Any) -> None:
    """Les champions vus pour la première fois : profil et images Data Dragon."""
    from django.conf import settings

    if not settings.DDRAGON_AUTO_SYNC or not Champion.objects.filter(synced_at__isnull=True).exists():
        return
    from .ddragon import sync_champions

    with run.report.section("Champions (Data Dragon)") as section:
        try:
            result = sync_champions(log=_section_logger(section))
        except Exception as exc:  # noqa: BLE001 - l'import reste bon sans les images
            section.warning(f"Data Dragon injoignable : {exc}")
            return
        section.success(f"{result['created']} champion(s) ajouté(s), {result['images']} image(s).")


@operation(
    name="esport.download_images",
    label=_("Télécharger les photos manquantes"),
    description=_(
        "Télécharge les photos des cartes et les logos qui manquent, sans "
        "réimporter les parties."
    ),
    icon="image",
    background=True,
)
def download_images(run: Any, ids: list[Any]) -> str:
    importer = LeaguepediaImporter()
    total = 0

    for split in Split.objects.filter(pk__in=ids):
        with run.report.section(split.name, url=_split_url(split)) as section:
            importer.log = _section_logger(section)
            teams = Team.objects.filter(cards__split=split).distinct()
            logos = importer.download_images(
                [(team, "logo_filename", "logo") for team in teams]
            )
            photos = download_card_images(split, importer)
            total += logos + photos
            section.info(f"{photos} photo(s), {logos} logo(s) téléchargé(s).")

    return f"{total} image(s) téléchargée(s)."


@operation(
    name="esport.recompute_splits",
    label=_("Recalculer stats et cartes"),
    description=_(
        "Recalcule les stats, les notes, les champions et les équipes à partir des "
        "données déjà importées, sans appel à Leaguepedia."
    ),
    icon="calculate",
)
def recompute_splits(run: Any, ids: list[Any]) -> str:
    splits = list(Split.objects.filter(pk__in=ids))

    for split in splits:
        with run.report.section(
            split.name, url=_split_url(split), isolated=True
        ) as section:
            stats = LeaguepediaImporter.compute_split_stats(split)
            created, updated = generate_cards(split)
            section.success(
                f"{stats} fiche(s) de stats, {created} carte(s) créée(s), "
                f"{updated} mise(s) à jour."
            )

    with run.report.section("Boosters") as section:
        for name in ensure_boosters():
            section.success(f"Booster « {name} » créé.")

    return f"{len(splits)} split(s) recalculé(s)."


@operation(
    name="esport.sync_leagues",
    label=_("Synchroniser les ligues"),
    description=_("Ajoute ou met à jour des ligues de Leaguepedia (nom, région, niveau)."),
    icon="sync",
    background=True,
)
def sync_leagues(run: Any, names: list[str] | None = None, primary_only: bool = False) -> str:
    created, updated = LeaguepediaImporter().sync_leagues(names or None, primary_only)
    if not created and not updated:
        return "Aucune ligue trouvée : vérifie les noms (LEC, LCK, LFL…)."
    return f"{created} ligue(s) ajoutée(s), {updated} mise(s) à jour."


@operation(
    name="esport.import_league",
    label=_("Importer depuis Leaguepedia"),
    description=_(
        "Cherche les tournois des ligues et années choisies, crée les "
        "splits, importe parties, joueurs et photos, génère les cartes et "
        "les boosters."
    ),
    icon="travel_explore",
    background=True,
)
def import_league(
    run: Any,
    leagues: list[str],
    years: list[int],
    name_like: str = "",
    include_qualifiers: bool = False,
    images: bool = True,
) -> str:
    importer = LeaguepediaImporter()
    label = f"Tournois : {', '.join(leagues)} — {', '.join(str(y) for y in years)}"
    return _import_tournaments(run, importer, label, leagues, years, name_like, include_qualifiers, images)


def _import_tournaments(
    run: Any,
    importer: LeaguepediaImporter,
    label: str,
    leagues: list[str],
    years: list[int] | None,
    name_like: str = "",
    include_qualifiers: bool = False,
    images: bool = True,
    skip_finished: bool = False,
    main_events_only: bool = False,
) -> str:
    """Trouve les tournois, crée les splits, puis importe ceux-ci.
    skip_finished : ne réimporte pas un split terminé avant son dernier import."""
    with run.report.section(label) as section:
        importer.log = _section_logger(section)
        rows = importer.discover(leagues, years, name_like or None)
        if main_events_only:
            rows = _main_events(rows, section)

        if not rows:
            section.error("Aucun tournoi trouvé pour ces ligues et ces années.")
            return "Aucun tournoi trouvé."

        tournaments = importer.save_tournaments(
            rows, include_qualifiers=include_qualifiers
        )
        for tournament in tournaments:
            note = "qualifications : pas de split" if tournament.split is None else f"split : {tournament.split}"
            section.info(tournament.name, note)

    # Les phases d'une même année d'une ligue locale vont ensemble.
    with run.report.section("Périodes") as section:
        for name in regroup_splits(log=_section_logger(section)):
            section.success(f"{name} : phases regroupées.")
    for tournament in tournaments:
        tournament.refresh_from_db()
    # Les années récentes d'abord : le jeu est jouable au plus vite.
    split_ids = [
        pk for pk, _ in sorted(
            Split.objects.filter(pk__in={t.split_id for t in tournaments if t.split_id}).values_list("pk", "year"),
            key=lambda row: (-row[1], row[0]),
        )
    ]
    skipped = 0
    if skip_finished:
        done = [
            s.pk for s in Split.objects.filter(pk__in=split_ids, last_imported_at__isnull=False, end_date__isnull=False)
            if s.last_imported_at.date() > s.end_date
        ]
        skipped = len(done)
        split_ids = [pk for pk in split_ids if pk not in done]
    run.note(f"{len(split_ids)} split(s) à importer")
    _import_splits(run, split_ids, images)

    message = f"{len(tournaments)} tournoi(s), {len(split_ids)} split(s) importé(s)."
    if skipped:
        message += f" {skipped} split(s) déjà à jour, sautés."
    return message


@operation(
    name="esport.import_everything",
    label=_("Importer tout le jeu"),
    description=_(
        "Toutes les années disponibles sur Leaguepedia de la LEC, la LCK, "
        "la LPL, la LCS (et leurs anciens noms), des First Stand, MSI, Worlds "
        "et de l'Esports World Cup. Les splits terminés et déjà importés sont "
        "sautés : on peut le relancer pour récupérer les nouveautés."
    ),
    icon="cloud_sync",
    background=True,
)
def import_everything(run: Any, images: bool = True, refresh: bool = False) -> str:
    from .catalog import FULL_IMPORT, full_import_q
    from .models import League

    importer = LeaguepediaImporter()
    with run.report.section("Ligues") as section:
        importer.log = _section_logger(section)
        created, updated = importer.sync_leagues(list(FULL_IMPORT))
        section.info(f"{created} ligue(s) ajoutée(s), {updated} mise(s) à jour.")
        leagues = sorted(League.objects.filter(full_import_q()).values_list("name", flat=True))
        if not leagues:
            section.error("Aucune de ces ligues trouvée sur Leaguepedia.")
            return "Aucune ligue trouvée."
        section.info(", ".join(leagues))
    with run.report.section("Nettoyage") as section:
        for name in _drop_empty_side_splits():
            section.info(f"{name} : retiré (qualifications, exhibition ou pas encore joué).")
    return _import_tournaments(
        run, importer, "Tournois : toutes les années", leagues, None, images=images,
        skip_finished=not refresh, main_events_only=True,
    )


def _main_events(rows: list[dict], section) -> list[dict]:
    """Sans les finales régionales, les exhibitions, ni les tournois pas
    encore commencés (Leaguepedia a déjà les pages des éditions futures)."""
    from django.utils import timezone

    from esport import scoring

    from .catalog import is_side_event

    today = timezone.localdate()
    kept, skipped = [], 0
    for row in rows:
        start = scoring.parse_date(row.get("DateStart"))
        year = scoring.parse_int(row.get("Year"), default=None)
        not_started = start > today if start else (not year or year > today.year)
        if is_side_event(row.get("Name") or row.get("OverviewPage")) or not_started:
            skipped += 1
            continue
        kept.append(row)
    if skipped:
        section.info(f"{skipped} tournoi(s) sauté(s) : qualifications, exhibitions ou pas encore commencés.")
    return kept


def _drop_empty_side_splits() -> list[str]:
    """Retire les splits sans aucune carte qui ne sont que des finales
    régionales, des exhibitions ou des tournois pas encore commencés
    (créés par un import précédent)."""
    from django.utils import timezone

    from .catalog import full_import_q, is_side_event
    from .models import League, Tournament

    today = timezone.localdate()
    removed = []
    splits = Split.objects.filter(cards__isnull=True, league__in=League.objects.filter(full_import_q()))
    for split in splits.prefetch_related("tournaments"):
        tournaments = list(split.tournaments.all())
        if any(t.appearances.exists() for t in tournaments):
            continue
        side = is_side_event(split.name) or (tournaments and all(is_side_event(t.name) for t in tournaments))
        future = not split.start_date or split.start_date > today
        if side or future:
            Tournament.objects.filter(pk__in=[t.pk for t in tournaments]).delete()
            removed.append(split.name)
            split.delete()
    return removed




@operation(
    name="esport.regroup_splits",
    label=_("Regrouper les phases par année"),
    description=_(
        "Pour chaque ligue locale, réunit les phases d'une même année "
        "(Cup, splits, playoffs) en une seule période. Les cartes "
        "possédées et les équipes suivent. L'international reste à part."
    ),
    icon="merge",
)
def regroup_splits_operation(run: Any) -> str:
    with run.report.section("Périodes") as section:
        names = regroup_splits(log=_section_logger(section))
        for name in names:
            section.success(f"{name} : phases regroupées.")
    with run.report.section("Boosters") as section:
        for name in ensure_boosters():
            section.success(f"Booster « {name} » créé.")
    return f"{len(names)} période(s) regroupée(s)." if names else "Rien à regrouper."


@operation(
    name="esport.sync_champions",
    label=_("Synchroniser les champions"),
    description=_(
        "Récupère sur Data Dragon (Riot) tous les champions : titre, "
        "classes, profil attaque / défense / magie, et leurs images."
    ),
    icon="sync",
    background=True,
)
def sync_champions_operation(run: Any, images: bool = True, force_images: bool = False) -> str:
    from .ddragon import sync_champions

    with run.report.section("Data Dragon") as section:
        result = sync_champions(images=images, force_images=force_images, log=_section_logger(section))
    return (
        f"{result['created']} champion(s) ajouté(s), {result['updated']} mis à jour, "
        f"{result['images']} image(s) téléchargée(s)."
    )


# --------------------------------------------------------------------- Automatique
# Les tâches planifiées (esport/automation.py, planning : esport/schedules.py).
from generic.tasks import managed_task  # noqa: E402

from . import automation  # noqa: E402

QUIET = ("page",)


def _automatic(run, work):
    try:
        with automation.single_run():
            return work()
    except automation.Busy:
        run.note("Un autre import automatique est en cours : rien fait cette fois.")
        return "Déjà en cours"


@managed_task(
    name="esport.discover_tournaments",
    label=_("Tournois à venir"),
    description=_(
        "Les tournois de cette année et de la suivante des 4 ligues majeures et des internationaux de Riot, "
        "avec leurs dates (sans les parties). Ouvre les compétitions Fantasy à venir."
    ),
    icon="event",
    announce=QUIET,
    report=QUIET,
)
def discover_tournaments_task(run: Any) -> str:
    def work():
        n = automation.discover_tournaments(LeaguepediaImporter(log=run.note))
        from cards import fantasy

        fantasy.sync_competitions()
        return f"{n} tournoi(s) à jour"

    return _automatic(run, work)


@managed_task(
    name="esport.sync_schedules",
    label=_("Calendrier des matchs"),
    description=_(
        "Le calendrier Leaguepedia des tournois en cours ou qui commencent dans les 10 jours : "
        "chaque match planifie l'import de ses parties après sa fin prévue."
    ),
    icon="calendar_month",
    announce=QUIET,
    report=QUIET,
)
def sync_schedules_task(run: Any) -> str:
    return _automatic(run, lambda: f"{automation.sync_schedules(LeaguepediaImporter(log=run.note))} match(s) au calendrier")


@managed_task(
    name="esport.import_results",
    label=_("Résultats des matchs"),
    description=_(
        "Importe les parties des matchs terminés (seulement les parties récentes de leur tournoi), "
        "puis met à jour les points de la Fantasy."
    ),
    icon="scoreboard",
    announce=QUIET,
    report=QUIET,
)
def import_results_task(run: Any) -> str:
    def work():
        result = automation.import_results(LeaguepediaImporter(log=run.note))
        comps = automation.refresh_fantasy(result["tournois"])
        return (f"{result['importés']} match(s) importé(s), {result['en attente']} en attente, "
                f"{result['abandonnés']} abandonné(s) ; {comps} compétition(s) Fantasy à jour")

    return _automatic(run, work)


@managed_task(
    name="esport.consolidate",
    label=_("Mise à jour de nuit"),
    description=_(
        "Les tournois en cours sans calendrier (import des parties récentes), puis les stats, cartes et "
        "photos des splits qui ont reçu des parties."
    ),
    icon="bedtime",
    announce=QUIET,
    report=("notification",),
    audience="staff",
)
def consolidate_task(run: Any) -> str:
    def work():
        importer = LeaguepediaImporter(log=run.note)
        extra = automation.unscheduled_imports(importer)
        automation.refresh_fantasy(extra)
        n = automation.consolidate(importer, log=run.note)
        return f"{len(extra)} tournoi(s) sans calendrier importé(s), {n} split(s) mis à jour"

    return _automatic(run, work)


try:
    from celery import shared_task
except ImportError:  # sans Celery (tests, développement) : pas de surveillance
    shared_task = None

if shared_task is not None:
    @shared_task(name="esport.watch_results", ignore_result=True)
    def watch_results() -> int:
        """Toutes les 10 min : une requête en base. Ne lance l'import (et
        n'écrit une exécution dans la gestion) que si un match est à importer."""
        if not automation.due_matches().exists():
            return 0
        from generic.tasks import launch
        from generic.tasks.models import TaskRun

        launch("esport.import_results", trigger=TaskRun.Trigger.SCHEDULE)
        return 1
