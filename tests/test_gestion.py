"""La gestion (django-generic) : opérations et pages propres au projet."""

import pytest
from django.urls import reverse

from cards.models import Card, Rarity
from esport.models import Split
from esport.tasks import recompute_splits

pytestmark = pytest.mark.django_db


def test_recompute_refreshes_cards_and_champions(split, admin_user):
    from cards.models import CardChampion

    CardChampion.objects.all().delete()

    run = recompute_splits.start(admin_user, ids=[split.pk])

    assert run.status == run.Status.SUCCESS, run.error
    assert CardChampion.objects.filter(card__split=split).exists()


def test_the_regroup_page_merges_phases(admin_client, split):
    response = admin_client.get(reverse("site:esport_split_regrouper"))
    assert response.status_code == 200


def test_the_card_preview_page_draws_the_card(admin_client, split):
    card = Card.objects.first()
    url = reverse("site:cards_card_apercu", args=[card.pk])

    response = admin_client.get(url)

    assert response.status_code == 200
    assert card.player.ign in response.content.decode()


def test_the_split_action_starts_the_recompute(admin_client, split):
    url = reverse("site:api_esport_split-actions")

    response = admin_client.post(
        url,
        {"action": "recompute", "ids": [split.pk]},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert Split.objects.get(pk=split.pk).cards.count() == 10


def test_the_import_operation_reports_each_split(admin_user, monkeypatch):
    from cards.services import ensure_default_rarities
    from esport import tasks
    from esport.importer import LeaguepediaImporter
    from tests.test_importer import FakeClient

    ensure_default_rarities()
    monkeypatch.setattr(
        tasks,
        "LeaguepediaImporter",
        lambda: LeaguepediaImporter(client=FakeClient()),
    )
    importer = LeaguepediaImporter(client=FakeClient(), log=lambda m: None)
    importer.save_tournaments(importer.discover_tournaments("LEC", 2025))
    split = Split.objects.get()

    run = tasks.import_splits.start(
        admin_user, background=False, ids=[split.pk]
    )

    assert run.status == run.Status.SUCCESS, run.error
    assert split.cards.count() == 10
    run.refresh_from_db()
    titles = [node["title"] for node in run.tree]
    assert titles == ["LEC 2025"]


def test_the_booster_list_counts_its_pool(split, rf, admin_user):
    from cards.models import Booster
    from cards.resources import BoosterResource
    from generic.sites import site

    every = Booster.objects.create(name="Tous")
    only = Booster.objects.create(name="Seulement le split")
    only.splits.add(split)
    request = rf.get("/")
    request.user = admin_user
    resource = site.get_resource(Booster)

    rows = {b.pk: b for b in resource.get_list_queryset(request)}

    assert isinstance(resource, BoosterResource)
    assert resource.pool_size(rows[every.pk]) == 10
    assert resource.pool_size(rows[only.pk]) == 10
    split.is_playable = False
    split.save()
    rows = {b.pk: b for b in resource.get_list_queryset(request)}
    assert resource.pool_size(rows[only.pk]) == 0


def test_importing_leagues_creates_splits_cards_and_region_boosters(
    admin_user, monkeypatch
):
    from cards.models import Booster
    from esport import tasks
    from esport.importer import LeaguepediaImporter
    from tests.test_importer import FakeClient

    monkeypatch.setattr(
        tasks,
        "LeaguepediaImporter",
        lambda: LeaguepediaImporter(client=FakeClient()),
    )

    run = tasks.import_league.start(
        admin_user,
        background=False,
        leagues=["LoL EMEA Championship"],
        years=[2025],
    )

    assert run.status == run.Status.SUCCESS, run.error
    split = Split.objects.get()
    assert split.cards.count() == 10
    region = Booster.objects.get(slug="emea-2025")
    assert region.name == "EMEA 2025" and not region.is_active
    assert region.card_pool().count() == 10
    assert Booster.objects.get(slug="base").is_active


def test_syncing_leagues_fills_the_league_list(admin_user, monkeypatch):
    from esport import tasks
    from esport.importer import LeaguepediaImporter
    from esport.models import League
    from tests.test_importer import FakeClient

    monkeypatch.setattr(
        tasks, "LeaguepediaImporter", lambda: LeaguepediaImporter(client=FakeClient())
    )

    run = tasks.sync_leagues.start(admin_user, background=False)

    assert run.status == run.Status.SUCCESS, run.error
    lck = League.objects.get(short_name="LCK")
    assert lck.region == "Korea" and lck.is_official and lck.level == "Primary"


def test_the_import_page_starts_the_operation(admin_client, monkeypatch):
    from esport import form_pages
    from esport.models import League

    lec = League.objects.create(name="LoL EMEA Championship", short_name="LEC", region="EMEA")
    League.objects.create(name="Mid-Season Invitational", short_name="MSI", region="International", level="Primary")
    started = {}

    class FakeRun:
        def get_absolute_url(self):
            return "/gestion/generic/taskrun/1/"

    def fake_start(request, **kwargs):
        started.update(kwargs)
        return FakeRun()

    monkeypatch.setattr(form_pages.import_league, "start", fake_start)
    url = reverse("site:esport_split_importer-une-ligue")

    assert admin_client.get(url).status_code == 200
    response = admin_client.post(
        url,
        {"leagues": [lec.pk], "years": [2025, 2026], "international": True, "images": False},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["_redirect"] == "/gestion/generic/taskrun/1/"
    assert started["leagues"] == ["LoL EMEA Championship", "Mid-Season Invitational"]
    assert started["years"] == [2025, 2026] and started["images"] is False


def test_the_import_page_needs_a_league(admin_client):
    url = reverse("site:esport_split_importer-une-ligue")
    response = admin_client.post(url, {"years": [2025]}, content_type="application/json")
    assert response.status_code == 400


def test_devworker_restarts_celery_when_the_code_changes(tmp_path, settings, monkeypatch):
    import os

    from esport.management.commands import devworker

    (tmp_path / "esport").mkdir()
    module = tmp_path / "esport" / "x.py"
    module.write_text("a = 1")
    settings.BASE_DIR = tmp_path
    started = []

    class FakeWorker:
        def __init__(self, command):
            started.append(command)
            self.stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout=None):
            return 0

    ticks = {"n": 0}

    def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 1:
            os.utime(module, (1, 1))  # le code change
        if ticks["n"] == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(devworker.subprocess, "Popen", FakeWorker)
    monkeypatch.setattr(devworker.time, "sleep", fake_sleep)
    monkeypatch.setattr(devworker.signal, "signal", lambda *a: None)

    try:
        devworker.Command().handle(interval=0)
    except KeyboardInterrupt:
        pass

    assert len(started) == 2
    assert "celery" in started[0] and "worker" in started[0]



def test_syncing_only_named_leagues_filters_the_query(admin_user, monkeypatch):
    from esport import tasks
    from esport.importer import LeaguepediaImporter
    from tests.test_importer import FakeClient

    client = FakeClient()
    seen = {}
    original = client.cargo

    def spy(tables, fields, where=None, **kw):
        seen[tables] = where
        return original(tables, fields, where=where, **kw)

    client.cargo = spy
    monkeypatch.setattr(tasks, "LeaguepediaImporter", lambda: LeaguepediaImporter(client=client))

    run = tasks.sync_leagues.start(admin_user, background=False, names=["LCK", "LEC"])

    assert run.status == run.Status.SUCCESS, run.error
    assert 'L.League_Short IN ("LCK", "LEC")' in seen["Leagues=L"]


def test_the_league_action_imports_the_current_year(admin_client, monkeypatch):
    from django.utils import timezone

    from esport import resources
    from esport.models import League

    lck = League.objects.create(name="LoL Champions Korea", short_name="LCK", region="Korea")
    started = {}
    monkeypatch.setattr(resources.import_league, "start", lambda request, **kw: started.update(kw))

    response = admin_client.post(
        reverse("site:api_esport_league-actions"),
        {"action": "import_current_year", "ids": [lck.pk]},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert started["leagues"] == ["LoL Champions Korea"]
    assert started["years"] == [timezone.localdate().year]


def test_the_league_list_says_which_leagues_are_imported(admin_client, split):
    from esport.models import League

    League.objects.create(name="LoL Champions Korea", short_name="LCK", region="Korea")
    response = admin_client.get(reverse("site:api_esport_league-list"), {"length": 50})
    assert response.status_code == 200, response.content
    rows = {row["short_name"]: row for row in response.json()["data"]}
    assert rows["LEC"]["imported"] is True
    assert rows["LCK"]["imported"] is False


def test_the_imported_leagues_preset_filters(admin_client, split):
    import json

    from esport.models import League

    League.objects.create(name="LoL Champions Korea", short_name="LCK", region="Korea")
    filters = {"match": "all", "conditions": [{"column": "imported", "operator": "is_true"}]}
    response = admin_client.get(
        reverse("site:api_esport_league-list"), {"length": 50, "filters": json.dumps(filters)}
    )
    assert response.status_code == 200, response.content
    assert [row["short_name"] for row in response.json()["data"]] == ["LEC"]


def test_scores_are_recomputed_once_when_the_rating_changes(split, settings, tmp_path):
    from django.core.management import call_command

    from esport.models import PlayerSplitStats
    from esport.scoring import SCORING_VERSION

    settings.MEDIA_ROOT = tmp_path
    PlayerSplitStats.objects.update(score=0)

    call_command("recompute_cards", "--si-besoin")
    assert PlayerSplitStats.objects.exclude(score=0).exists()
    assert (tmp_path / ".version-notes").read_text() == str(SCORING_VERSION)

    PlayerSplitStats.objects.update(score=0)
    call_command("recompute_cards", "--si-besoin")  # déjà à jour : rien
    assert not PlayerSplitStats.objects.exclude(score=0).exists()


def test_importing_everything_skips_finished_splits(admin_user, monkeypatch):
    from esport import tasks
    from esport.importer import LeaguepediaImporter
    from tests.test_importer import FakeClient

    client = FakeClient()
    monkeypatch.setattr(tasks, "LeaguepediaImporter", lambda: LeaguepediaImporter(client=client))

    run = tasks.import_everything.start(admin_user, background=False, images=False)
    assert run.status == run.Status.SUCCESS, run.error
    split = Split.objects.get()
    assert split.cards.count() == 10 and split.last_imported_at

    before = sum(1 for c in client.calls if c.startswith("ScoreboardPlayers"))
    run = tasks.import_everything.start(admin_user, background=False, images=False)
    assert run.status == run.Status.SUCCESS, run.error
    assert "déjà à jour" in run.summary
    assert sum(1 for c in client.calls if c.startswith("ScoreboardPlayers")) == before


def test_the_import_everything_page_opens(admin_client):
    assert admin_client.get(reverse("site:esport_split_tout-importer")).status_code == 200


def test_the_full_import_leaves_out_side_events_and_future_editions(db):
    import datetime

    from esport.models import League, Tournament
    from esport.tasks import _drop_empty_side_splits, _main_events

    class Section:
        def info(self, *a, **k):
            pass

    today = datetime.date.today()
    rows = [
        {"Name": "Worlds 2025", "DateStart": "2025-10-01", "Year": "2025"},
        {"Name": "China Regional Finals 2018", "DateStart": "2018-08-01", "Year": "2018"},
        {"Name": "LPL 2022 Regional Finals Showmatch", "DateStart": "2022-09-01", "Year": "2022"},
        {"Name": "Worlds 2099", "DateStart": f"{today.year + 1}-10-01", "Year": str(today.year + 1)},
        {"Name": "MSI sans date", "DateStart": "", "Year": str(today.year + 1)},
    ]
    assert [r["Name"] for r in _main_events(rows, Section())] == ["Worlds 2025"]

    worlds = League.objects.create(name="World Championship", short_name="WCS", region="International")
    old = Split.objects.create(league=worlds, name="China Regional Finals 2018", year=2018,
                               start_date=datetime.date(2018, 8, 1))
    Tournament.objects.create(overview_page="CRF 2018", name="China Regional Finals 2018", league=worlds, split=old)
    future = Split.objects.create(league=worlds, name="Worlds 2099", year=2099)
    kept = Split.objects.create(league=worlds, name="Worlds 2025", year=2025, start_date=datetime.date(2025, 10, 1))
    other = League.objects.create(name="Ma ligue", short_name="ML", region="EMEA")
    mine = Split.objects.create(league=other, name="Ma ligue sans date", year=2099)

    assert set(_drop_empty_side_splits()) == {"China Regional Finals 2018", "Worlds 2099"}
    assert set(Split.objects.values_list("pk", flat=True)) == {kept.pk, mine.pk}
    assert not Tournament.objects.exists()
    assert future.pk not in Split.objects.values_list("pk", flat=True)
