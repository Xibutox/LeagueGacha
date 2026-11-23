"""Les mises à jour automatiques (esport/automation.py), avec un faux client
Leaguepedia : calendrier, import après les matchs, nouvelles tentatives,
import partiel qui garde les identifiants des parties."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from esport import automation, schedules
from esport.importer import LeaguepediaImporter
from esport.models import GameAppearance, League, ScheduledMatch, Split, Tournament

pytestmark = pytest.mark.django_db

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]
TEAMS = {"G2 Esports": "G2", "Fnatic": "FNC"}


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=dt_timezone.utc)


def game_rows(game_id, when):
    rows = []
    for t, team in enumerate(TEAMS):
        for role in ROLES:
            name = f"{TEAMS[team]}{role}"
            rows.append({"GameId": game_id, "Link": name, "Name": name, "Team": team,
                         "TeamVs": list(TEAMS)[1 - t], "Role": role, "Champion": "Azir", "Kills": "3",
                         "Deaths": "2", "Assists": "5", "Gold": "11000", "CS": "250", "Damage": "15000",
                         "VisionScore": "30", "TeamKills": "15", "TeamGold": "55000",
                         "PlayerWin": "Yes" if t == 0 else "No", "Side": "1",
                         "DateTimeUTC": when.strftime("%Y-%m-%d %H:%M:%S"), "GameLength": "31"})
    return rows


class FakeClient:
    """Leaguepedia : un calendrier, et les scoreboards publiés au fil de l'eau."""

    def __init__(self):
        self.games = []  # lignes publiées
        self.schedule = []
        self.scoreboard_queries = []

    def cargo(self, tables, fields, where=None, **kw):
        if tables.startswith("MatchSchedule"):
            return self.schedule
        if tables.startswith("ScoreboardPlayers"):
            self.scoreboard_queries.append(where)
            since = where.split(">= ")[1].strip("'\"") if ">=" in where else None
            return [r for r in self.games if since is None or r["DateTimeUTC"] >= since]
        if tables.startswith("PlayerRedirects"):
            return [{"AllName": f"{s}{r}", "OverviewPage": f"{s}{r}", "IGN": f"{s}{r}", "Image": "",
                     "Name": "", "NativeName": "", "Country": "France", "Nationality": "", "Birthdate": "",
                     "Role": r, "Team": t, "IsRetired": "0"} for t, s in TEAMS.items() for r in ROLES]
        if tables.startswith("Players") or tables.startswith("PlayerImages"):
            return []
        raise AssertionError(tables)

    def image_infos(self, filenames, width=None):
        return {}


@pytest.fixture
def lec():
    league = League.objects.create(name="LoL EMEA Championship", short_name="LEC", region="EMEA")
    split = Split.objects.create(league=league, name="LEC 2027 Winter", slug="lec-2027-winter", year=2027)
    return Tournament.objects.create(overview_page="LEC/2027 Season/Winter Season", name="LEC 2027 Winter",
                                     league=league, split=split, year=2027, start_date=date(2027, 1, 16),
                                     end_date=date(2027, 3, 1))


def test_the_schedule_plans_an_import_after_each_match(lec):
    client = FakeClient()
    client.schedule = [{"MatchId": "LEC_W1_1", "OverviewPage": lec.overview_page, "DateTimeUTC": "2027-01-16 16:00:00",
                        "Team1": "G2 Esports", "Team2": "Fnatic", "BestOf": "3", "Team1Score": "", "Team2Score": ""}]
    importer = LeaguepediaImporter(client=client, log=lambda m: None)
    assert automation.sync_schedules(importer, utc(2027, 1, 10, 8)) == 1
    match = ScheduledMatch.objects.get()
    # Bo3 : 3 × 45 min, + 30 min pour que Leaguepedia ait les scoreboards.
    assert match.next_attempt == utc(2027, 1, 16, 16) + timedelta(minutes=135 + 30)
    # Le match est décalé d'une heure : la tentative suit.
    client.schedule[0]["DateTimeUTC"] = "2027-01-16 17:00:00"
    automation.sync_schedules(importer, utc(2027, 1, 11, 8))
    match.refresh_from_db()
    assert match.next_attempt == utc(2027, 1, 16, 17) + timedelta(minutes=165)


def test_results_are_imported_when_due_then_retried(lec):
    client = FakeClient()
    client.schedule = [{"MatchId": "LEC_W1_1", "OverviewPage": lec.overview_page, "DateTimeUTC": "2027-01-16 16:00:00",
                        "Team1": "G2 Esports", "Team2": "Fnatic", "BestOf": "3", "Team1Score": "2", "Team2Score": "0"}]
    importer = LeaguepediaImporter(client=client, log=lambda m: None)
    automation.sync_schedules(importer, utc(2027, 1, 10, 8))
    # Avant la fin prévue : rien à faire, aucune requête.
    assert not automation.due_matches(utc(2027, 1, 16, 18)).exists()
    # Leaguepedia n'a qu'une des deux parties : nouvel essai plus tard.
    client.games = game_rows("G1", utc(2027, 1, 16, 16, 5))
    result = automation.import_results(importer, utc(2027, 1, 16, 19))
    match = ScheduledMatch.objects.get()
    assert result["en attente"] == 1 and match.attempts == 1
    assert match.next_attempt == utc(2027, 1, 16, 16) + timedelta(minutes=135) + automation.ATTEMPTS[1]
    assert "DateTime_UTC >= " in client.scoreboard_queries[-1]  # seulement les parties récentes
    ids = set(GameAppearance.objects.values_list("pk", flat=True))
    # La seconde partie arrive : le match est importé, la première partie garde ses identifiants.
    client.games += game_rows("G2", utc(2027, 1, 16, 17))
    result = automation.import_results(importer, match.next_attempt)
    match.refresh_from_db()
    assert result["importés"] == 1 and match.status == ScheduledMatch.Status.IMPORTED
    assert ids <= set(GameAppearance.objects.values_list("pk", flat=True))
    assert GameAppearance.objects.values("game_id").distinct().count() == 2
    lec.refresh_from_db()
    assert lec.games_imported == 2 and lec.last_imported_at is not None


def test_a_match_never_published_is_given_up(lec):
    client = FakeClient()
    client.schedule = [{"MatchId": "X", "OverviewPage": lec.overview_page, "DateTimeUTC": "2027-01-16 16:00:00",
                        "Team1": "G2 Esports", "Team2": "Fnatic", "BestOf": "1"}]
    importer = LeaguepediaImporter(client=client, log=lambda m: None)
    automation.sync_schedules(importer, utc(2027, 1, 10, 8))
    for _ in automation.ATTEMPTS:
        automation.import_results(importer, utc(2027, 1, 20, 0))
    assert ScheduledMatch.objects.get().status == ScheduledMatch.Status.MISSING


def test_the_night_consolidates_splits_that_received_games(lec):
    client = FakeClient()
    client.games = game_rows("G1", utc(2027, 1, 16, 16))
    importer = LeaguepediaImporter(client=client, log=lambda m: None)
    from cards.services import ensure_default_rarities

    ensure_default_rarities()
    # Pas de calendrier : la nuit importe les parties récentes du tournoi en cours.
    assert automation.unscheduled_imports(importer, utc(2027, 1, 17, 3)) == [lec]
    assert list(automation.splits_to_consolidate()) == [lec.split]
    assert automation.consolidate(importer) == 1
    assert lec.split.cards.count() == 10
    assert not automation.splits_to_consolidate().exists()


def test_one_automatic_import_at_a_time():
    with automation.single_run():
        with pytest.raises(automation.Busy):
            with automation.single_run():
                pass
    with automation.single_run():  # libéré
        pass


def test_the_schedule_is_planned_when_beat_is_installed():
    assert {t for _, t, _, _ in schedules.SCHEDULES} >= {"esport.watch_results", "esport.sync_schedules",
                                                          "esport.consolidate", "cards.fantasy_hourly"}
    if not schedules.available():
        assert schedules.ensure_schedules() == []
        return
    from django_celery_beat.models import PeriodicTask

    assert len(schedules.ensure_schedules()) == len(schedules.SCHEDULES)
    assert schedules.ensure_schedules() == []  # déjà en place
    assert PeriodicTask.objects.filter(task="esport.watch_results", interval__every=10).exists()
