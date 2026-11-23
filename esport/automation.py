"""Les mises à jour automatiques depuis Leaguepedia, planifiées par les
tâches de esport/tasks.py et cards/tasks.py (django-celery-beat).

Principe : charger le serveur le moins possible tout en ayant les
résultats vite.

1. Tournois (2 fois par semaine) : les tournois de l'année et de la
   suivante des 4 majeures et des internationaux de Riot, avec leurs dates.
   Quelques requêtes, aucune partie.
2. Calendrier (2 fois par jour) : les matchs programmés des tournois en
   cours ou qui commencent dans les 10 jours (table MatchSchedule).
3. Résultats (toutes les 15 min, mais presque toujours rien à faire) : un
   match dont la fin prévue est passée (début + Bo × 45 min, + 30 min de
   marge pour Leaguepedia) déclenche l'import des seules parties récentes
   de son tournoi. S'il manque encore des parties, nouvel essai 1 h, 2 h,
   4 h, 8 h puis 24 h après la fin prévue. Les matchs d'un même tournoi
   arrivés à échéance ensemble partagent un import.
4. Nuit (04:30) : stats des splits qui ont reçu des parties dans la
   journée, cartes et photos ; et, pour un tournoi en cours dont
   Leaguepedia n'a pas de calendrier, un import des parties récentes.

Le worker Celery fait un calcul à la fois, en priorité basse : les pages
passent toujours avant.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import F, Max, Q
from django.utils import timezone

from . import scoring
from .catalog import FULL_IMPORT, EWC, is_side_event, major_leagues_q, riot_events_q
from .importer import LeaguepediaImporter
from .models import GameAppearance, League, ScheduledMatch, Split, Tournament

logger = logging.getLogger(__name__)

#: Durée prévue d'une partie, pour estimer la fin d'un match.
GAME_MINUTES = 45
#: Délais d'import après la fin prévue : premier essai, puis les suivants.
ATTEMPTS = [timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2), timedelta(hours=4),
            timedelta(hours=8), timedelta(hours=24)]
#: Calendrier : tournois qui commencent dans ces jours, ou finis depuis.
AHEAD_DAYS = 10
BEHIND_DAYS = 2
#: Un seul import automatique à la fois.
LOCK_SECONDS = 45 * 60


def tracked_leagues():
    """Les 4 majeures et les internationaux de Riot (pas l'Esports World Cup)."""
    return League.objects.filter(major_leagues_q() | riot_events_q())


def tracked_tournaments(now: datetime | None = None):
    """Les tournois en cours, qui commencent bientôt ou viennent de finir."""
    day = timezone.localtime(now or timezone.now()).date()
    return (Tournament.objects.filter(league__in=tracked_leagues(), is_qualifier=False,
                                      start_date__lte=day + timedelta(days=AHEAD_DAYS),
                                      end_date__gte=day - timedelta(days=BEHIND_DAYS))
            .select_related("league"))


def expected_end(start: datetime, best_of: int) -> datetime:
    return start + timedelta(minutes=GAME_MINUTES * max(1, best_of or 1))


# --------------------------------------------------------------------- Tournois
def discover_tournaments(importer: LeaguepediaImporter, now: datetime | None = None) -> int:
    """Les tournois de cette année et de la suivante (dates comprises)."""
    year = timezone.localtime(now or timezone.now()).year
    names = [n for n in FULL_IMPORT if n not in EWC]
    leagues = sorted(set(League.objects.filter(major_leagues_q() | riot_events_q()).values_list("name", flat=True))
                     | set(names))
    rows = importer.discover(leagues, [year, year + 1])
    rows = [r for r in rows if not is_side_event(r.get("Name") or r.get("OverviewPage"))]
    return len(importer.save_tournaments(rows))


# --------------------------------------------------------------------- Calendrier
def sync_schedules(importer: LeaguepediaImporter, now: datetime | None = None) -> int:
    """Met à jour le calendrier des tournois suivis. Un match déplacé est
    replanifié ; un match déjà importé ne l'est pas à nouveau."""
    tournaments = {t.overview_page: t for t in tracked_tournaments(now)}
    if not tournaments:
        return 0
    rows = importer.fetch_schedule(tournaments.values())
    saved = 0
    for row in rows:
        tournament = tournaments.get(row.get("OverviewPage"))
        start = scoring.parse_datetime_utc(row.get("DateTimeUTC"))
        if tournament is None or not row.get("MatchId") or start is None:
            continue
        best_of = scoring.parse_int(row.get("BestOf"), default=1) or 1
        values = {
            "tournament": tournament, "start": start, "best_of": best_of,
            "team1": row.get("Team1") or "", "team2": row.get("Team2") or "",
            "team1_score": scoring.parse_optional_int(row.get("Team1Score")),
            "team2_score": scoring.parse_optional_int(row.get("Team2Score")),
        }
        match = ScheduledMatch.objects.filter(match_id=row["MatchId"]).first()
        if match is None:
            ScheduledMatch.objects.create(match_id=row["MatchId"], attempts=0,
                                          next_attempt=expected_end(start, best_of) + ATTEMPTS[0], **values)
        else:
            moved = match.start != start or match.best_of != best_of
            for k, v in values.items():
                setattr(match, k, v)
            if moved and match.status != ScheduledMatch.Status.IMPORTED:
                match.status, match.attempts = ScheduledMatch.Status.SCHEDULED, 0
                match.next_attempt = expected_end(start, best_of) + ATTEMPTS[0]
            match.save()
        saved += 1
    return saved


# --------------------------------------------------------------------- Résultats
def games_needed(match: ScheduledMatch) -> int:
    """Le nombre de parties attendues : le score s'il est connu, sinon
    au moins une, et la moitié d'un Bo (arrondie au-dessus) pour un Bo3/Bo5."""
    if match.team1_score is not None and match.team2_score is not None and (match.team1_score or match.team2_score):
        return match.team1_score + match.team2_score
    return max(1, (match.best_of + 1) // 2)


def games_found(match: ScheduledMatch) -> int:
    qs = GameAppearance.objects.filter(tournament=match.tournament,
                                       played_at__gte=match.start - timedelta(minutes=30),
                                       played_at__lte=expected_end(match.start, match.best_of) + timedelta(hours=3))
    teams = [t for t in (match.team1, match.team2) if t]
    if teams:
        qs = qs.filter(Q(team__name__in=teams) | Q(opponent__in=teams))
    return qs.values("game_id").distinct().count()


def due_matches(now: datetime | None = None):
    return (ScheduledMatch.objects.filter(status=ScheduledMatch.Status.SCHEDULED,
                                          next_attempt__lte=now or timezone.now())
            .select_related("tournament"))


def import_results(importer: LeaguepediaImporter, now: datetime | None = None) -> dict:
    """Importe les parties des matchs arrivés à échéance, un import par
    tournoi. Renvoie {"tournois": [...], "importés": n, "en attente": n}."""
    now = now or timezone.now()
    by_tournament = defaultdict(list)
    for match in due_matches(now):
        by_tournament[match.tournament].append(match)
    result = {"tournois": [], "importés": 0, "en attente": 0, "abandonnés": 0}
    for tournament, matches in by_tournament.items():
        since = min(m.start for m in matches) - timedelta(hours=1)
        importer.import_tournament_games(tournament, since=since)
        result["tournois"].append(tournament)
        for match in matches:
            if games_found(match) >= games_needed(match):
                match.status, match.imported_at, match.next_attempt = ScheduledMatch.Status.IMPORTED, now, None
                result["importés"] += 1
            else:
                match.attempts += 1
                if match.attempts < len(ATTEMPTS):
                    match.next_attempt = expected_end(match.start, match.best_of) + ATTEMPTS[match.attempts]
                    result["en attente"] += 1
                else:
                    match.status, match.next_attempt = ScheduledMatch.Status.MISSING, None
                    result["abandonnés"] += 1
            match.save(update_fields=["status", "attempts", "next_attempt", "imported_at", "updated_at"])
    return result


def refresh_fantasy(tournaments) -> int:
    """Recalcule tout de suite les points des compétitions Fantasy de ces tournois."""
    from cards import fantasy
    from cards.models import FantasyCompetition

    comps = FantasyCompetition.objects.filter(tournaments__in=list(tournaments)).distinct()
    for comp in comps:
        fantasy.advance(comp)
    return len(comps)


# --------------------------------------------------------------------- Nuit
def unscheduled_imports(importer: LeaguepediaImporter, now: datetime | None = None) -> list[Tournament]:
    """Les tournois en cours sans calendrier sur Leaguepedia : import des
    parties des 2 derniers jours (ou de tout le tournoi la première fois)."""
    now = now or timezone.now()
    day = timezone.localtime(now).date()
    done = []
    for t in tracked_tournaments(now).filter(start_date__lte=day).exclude(schedule__isnull=False):
        last = t.appearances.aggregate(last=Max("played_at"))["last"]
        importer.import_tournament_games(t, since=(last - timedelta(days=2)) if last else None)
        done.append(t)
    return done


def splits_to_consolidate():
    """Les splits dont un tournoi a reçu des parties depuis leur dernière mise à jour."""
    stale = Q(last_imported_at__isnull=True) | Q(tournaments__last_imported_at__gt=F("last_imported_at"))
    return (Split.objects.filter(tournaments__appearances__isnull=False).filter(stale).distinct()
            .select_related("league"))


def consolidate(importer: LeaguepediaImporter, log=lambda m: None) -> int:
    """Stats, cartes et photos des splits qui ont reçu des parties."""
    from cards.services import download_card_images, ensure_boosters, generate_cards

    from .models import Player

    count = 0
    for split in splits_to_consolidate():
        players = list(Player.objects.filter(appearances__tournament__split=split, photo="").distinct())
        if players:
            importer.sync_player_images(players)
        n = importer.compute_split_stats(split)
        created, updated = generate_cards(split)
        photos = download_card_images(split, importer)  # seulement les photos absentes
        split.last_imported_at = timezone.now()
        split.save(update_fields=["last_imported_at"])
        log(f"{split.name} : {n} fiches de stats, {created} carte(s) créée(s), {updated} mise(s) à jour, {photos} photo(s)")
        count += 1
    if count:
        ensure_boosters()
    return count


# --------------------------------------------------------------------- Verrou
class Busy(Exception):
    pass


class single_run:
    """Un seul import automatique à la fois (verrou dans Redis)."""

    def __init__(self, name: str = "esport:auto"):
        self.key = name

    def __enter__(self):
        if not cache.add(self.key, 1, LOCK_SECONDS):
            raise Busy()
        return self

    def __exit__(self, *exc):
        cache.delete(self.key)
        return False
