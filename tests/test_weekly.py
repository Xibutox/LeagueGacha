"""Équipe de la semaine (méta hebdomadaire) et équipe de cœur."""
from datetime import date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from cards import weekly
from cards.models import Card, Roster, Supporter, WeeklyMeta
from cards.services import evaluate_roster, leaderboard, save_roster, set_supporter_team
from esport.models import Champion, Team

from .test_game import bob, champion_ids, full_team, give, owned  # noqa: F401  (fixtures)


def test_weeks_start_on_monday_midnight_in_paris():
    sunday_night = timezone.make_aware(datetime(2026, 11, 22, 23, 59))  # dimanche, heure de Paris
    monday = timezone.make_aware(datetime(2026, 11, 23, 0, 0))
    assert weekly.week_start(sunday_night) == date(2026, 11, 16)
    assert weekly.week_start(monday) == date(2026, 11, 23)
    assert weekly.week_end(date(2026, 11, 16)) == monday


def test_offmeta_bonus_grows_when_a_champion_is_less_played():
    assert weekly.offmeta_bonus(0) == 25
    assert weekly.offmeta_bonus(10) > weekly.offmeta_bonus(200) > weekly.offmeta_bonus(1000)
    assert weekly.offmeta_bonus(5000) == 0


def test_the_meta_has_five_different_kinds_and_is_stable(split):
    meta = weekly.meta_for(date(2026, 11, 16))
    assert set(meta.rules) == {"Top", "Jungle", "Mid", "Bot", "Support"}
    kinds = [r["kind"] for r in meta.rules.values()]
    assert len(set(kinds)) == 5
    assert all(r["label"] and r["bonus"] > 0 for r in meta.rules.values())
    # Le même tirage pour la même semaine.
    assert weekly.generate_rules(date(2026, 11, 16)) == meta.rules
    assert weekly.meta_for(date(2026, 11, 16)).pk == meta.pk


def _force_rule(week, role, **rule):
    meta = weekly.meta_for(week)
    rules = dict(meta.rules)
    rules[role] = rule
    meta.rules = rules
    meta.save()
    return meta


def test_a_card_matching_the_meta_scores_more(bob, full_team):  # noqa: F811
    week = weekly.week_start()
    top = Card.objects.get(pk=full_team["Top"]["card"])
    for role in ("Jungle", "Mid", "Bot", "Support"):
        _force_rule(week, role, kind="underdog", value=0, bonus=20, on_card=True, label="x")
    _force_rule(week, "Top", kind="team", value=top.team_id, bonus=20, on_card=True, label="Joueur de G2")
    plain = evaluate_roster(bob, full_team)
    weekly_result = evaluate_roster(bob, full_team, week=week)
    assert weekly_result["slots"]["Top"]["meta_bonus"] == 20
    assert all(e["meta_bonus"] == 20 for e in weekly_result["slots"]["Top"]["champions"])
    assert weekly_result["slots"]["Mid"]["meta_bonus"] == 0
    assert weekly_result["score"] > plain["score"]


def test_champion_bonuses_apply_per_champion(bob, full_team, champion_ids):  # noqa: F811
    week = weekly.week_start()
    tank = Champion.objects.get(pk=full_team["Mid"]["champions"][0])
    tank.tags = ["Tank"]
    tank.save()
    _force_rule(week, "Mid", kind="class", value="Tank", bonus=15, on_card=False, label="Tanks")
    result = evaluate_roster(bob, full_team, week=week)
    bonuses = [e["meta_bonus"] for e in result["slots"]["Mid"]["champions"]]
    assert bonuses[0] == 15 and bonuses[1:] == [0, 0, 0, 0]
    # Les champions fictifs des tests n'ont jamais été joués : bonus maximal.
    _force_rule(week, "Bot", kind="offmeta", value=1500, bonus=25, on_card=False, label="Peu joués")
    never_played = Champion.objects.create(name="Jamais Joué", key="JamaisJoue")
    assert weekly.champion_bonuses(weekly.meta_for(week).rules)["Bot"][never_played.pk] == 25


def test_the_weekly_roster_copies_the_best_permanent_one(bob, full_team):  # noqa: F811
    permanent = save_roster(bob, "Ma team", full_team)
    week = weekly.week_start()
    roster = weekly.weekly_roster(bob, week)
    assert roster.pk != permanent.pk and roster.week == week
    assert roster.score is not None
    assert {s.role: s.card_id for s in roster.slots.all()} == {s.role: s.card_id for s in permanent.slots.all()}
    # Une seule équipe de la semaine, et elle ne compte pas au classement général.
    assert weekly.weekly_roster(bob, week).pk == roster.pk
    assert [r.pk for _, r in leaderboard()] == [permanent.pk]
    assert [r.pk for _, r in leaderboard(week=week)] == [roster.pk]


def test_ensure_weekly_rosters_fills_the_weekly_ranking(bob, full_team):  # noqa: F811
    save_roster(bob, "Ma team", full_team)
    assert weekly.ensure_weekly_rosters() == 1
    assert weekly.ensure_weekly_rosters() == 0
    assert Roster.objects.filter(user=bob, week=weekly.week_start()).count() == 1


def test_supporter_team_adds_one_percent_per_player(bob, full_team):  # noqa: F811
    roster = save_roster(bob, "Ma team", full_team)
    base = roster.score
    cards = Card.objects.filter(pk__in=[p["card"] for p in full_team.values()]).select_related("team")
    team = cards[0].team
    fans = sum(1 for c in cards if c.team_id == team.pk)
    set_supporter_team(bob, team)
    roster.refresh_from_db()
    assert roster.details["supporter"] == {"team": team.name, "players": fans, "bonus": fans}
    assert roster.score == pytest.approx(round(base * (1 + fans / 100), 1), abs=0.11)
    set_supporter_team(bob, None)
    roster.refresh_from_db()
    assert roster.score == base


def test_weekly_pages(client, bob, full_team):  # noqa: F811
    save_roster(bob, "Ma team", full_team)
    client.force_login(bob)
    page = client.get(reverse("cards:weekly"))
    assert page.status_code == 200
    assert "Méta du" in page.content.decode()
    roster = Roster.objects.get(user=bob, week=weekly.week_start())
    assert client.get(reverse("cards:roster_edit", args=[roster.pk])).status_code == 200
    ranking = client.get(reverse("cards:ranking") + "?semaine=1")
    assert ranking.status_code == 200 and "de la semaine" in ranking.content.decode()
    preview = client.post(reverse("cards:roster_preview"), {"picks": full_team, "week": True},
                          content_type="application/json").json()
    assert preview["week"] == weekly.week_start().isoformat() and preview["complete"]
    # Équipe de cœur par son nom.
    team = Team.objects.get(name="G2 Esports")
    response = client.post(reverse("cards:supporter"), {"team": "g2 esports", "next": "/equipes/"})
    assert response.status_code == 302
    assert Supporter.objects.get(user=bob).team == team
    # Une équipe de la semaine ne se supprime pas.
    assert client.post(reverse("cards:roster_delete", args=[roster.pk])).status_code == 404


def test_a_past_weekly_roster_is_read_only(client, bob, full_team):  # noqa: F811
    roster = save_roster(bob, "Vieille", full_team, week=date(2020, 1, 6))
    client.force_login(bob)
    response = client.get(reverse("cards:roster_edit", args=[roster.pk]))
    assert response.status_code == 302 and response.url == reverse("cards:weekly")
    assert WeeklyMeta.objects.filter(week=date(2020, 1, 6)).exists()
