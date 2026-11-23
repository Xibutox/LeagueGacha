from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from cards import fantasy, fantasy_sim
from cards.models import (
    Booster,
    BoosterGift,
    Card,
    FantasyCompetition,
    FantasyLineup,
    FantasyWeek,
    Rarity,
    UserCard,
)
from cards.services import GameError
from esport.models import GameAppearance, Split, Tournament

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]


def paris(y, m, d, h=12):
    return timezone.make_aware(datetime(y, m, d, h))


# --------------------------------------------------------------------- Barème
def test_game_points_follow_the_scale():
    base, parts = fantasy.game_points({"kills": 4, "deaths": 1, "assists": 6, "cs": 300, "vision": 25,
                                       "win": True, "length": 28})
    assert base == 12 + 12 - 1 + 3 + 1 + 2 + 2
    assert ("victoire", 2) in parts
    support, _ = fantasy.game_points({"kills": 1, "deaths": 2, "assists": 12, "cs": 40, "vision": 80,
                                      "win": True, "length": 35})
    assert support == pytest.approx(3 + 24 - 2 + 0.4 + 3.2 + 2 + 2, abs=0.1)  # 10 assists : +2, pas de victoire rapide


def test_overperformance_rewards_players_above_their_average():
    assert fantasy.overperformance(34, 30) == 2
    assert fantasy.overperformance(34, 20) == 7
    assert fantasy.overperformance(45, 15) == 10  # plafond
    assert fantasy.overperformance(12, 30) == 0  # jamais de malus
    assert fantasy.overperformance(30, None) == 0


def test_weeks_run_wednesday_to_tuesday():
    assert fantasy.wednesday(date(2026, 11, 23)) == date(2026, 11, 18)  # lundi → mercredi d'avant
    assert fantasy.wednesday(date(2026, 11, 24)) == date(2026, 11, 18)  # mardi
    assert fantasy.wednesday(date(2026, 11, 25)) == date(2026, 11, 25)
    assert fantasy.diversity_bonus(["G2", "G2", "FNC", "", "KC"]) == 10
    assert fantasy.diversity_bonus(["A", "B", "C", "D", "E"]) == 20


# --------------------------------------------------------------------- Ligue réelle
@pytest.fixture
def lec(split):
    """Le split LEC 2027 : il commence le samedi 16 janvier 2027 (premier
    mercredi fantasy : le 13)."""
    new = Split.objects.create(league=split.league, name="LEC 2027 Winter", year=2027, split_label="Winter")
    tournament = Tournament.objects.create(overview_page="LEC/2027 Winter", name="LEC 2027 Winter",
                                           league=split.league, split=new, year=2027,
                                           start_date=date(2027, 1, 16), end_date=date(2027, 3, 1))
    return tournament


@pytest.fixture
def bob(split):
    user = get_user_model().objects.create_user("bob", password="mdp-bob-123")
    rarity = Rarity.objects.order_by("order").first()
    for card in Card.objects.filter(split=split):
        UserCard.objects.create(user=user, card=card, rarity=rarity)
    return user


def comp_for(now):
    fantasy.sync_competitions(now)
    return FantasyCompetition.objects.get(kind="league")


def test_a_league_competition_comes_from_tournament_dates(lec):
    comp = comp_for(paris(2027, 1, 5))
    assert comp.name == "LEC 2027 Winter" and comp.start == date(2027, 1, 13) and comp.end == date(2027, 3, 1)
    assert fantasy.tab_label(comp) == "LEC"
    # Avant les premiers matchs : les joueurs du split précédent.
    pool = fantasy.competition_pool(comp)
    assert len(pool) == 10 and {p["role"] for p in pool.values()} == set(ROLES)
    week = fantasy.editable_week(comp, paris(2027, 1, 5))
    assert week.start == date(2027, 1, 13) and week.number == 1
    assert week.players.count() == 10 and week.cap > 0
    # Rien de réel ces deux semaines-ci : l'international fictif les occupe,
    # et s'arrête la veille de la LEC.
    assert FantasyCompetition.objects.get(kind="fictional").end == date(2027, 1, 12)
    fantasy.sync_competitions(paris(2027, 1, 7))  # la LEC est la semaine suivante : pas de prolongation
    assert FantasyCompetition.objects.get(kind="fictional").end == date(2027, 1, 12)


def lineup_ids(split):
    by_role = {c.role: c.player_id for c in Card.objects.filter(split=split).order_by("player__ign")}
    sub = Card.objects.filter(split=split).exclude(player_id__in=by_role.values()).first().player_id
    return {**by_role, "sub": sub}


def test_lineup_rules(lec, bob, split):
    now = paris(2027, 1, 5)
    comp = comp_for(now)
    week = fantasy.editable_week(comp, now)
    assert 100 < week.cap < 250  # 6 × la moyenne × 1,1
    week.cap = 1000
    picks = lineup_ids(split)
    lineup = fantasy.save_lineup(bob, week, picks, "Mid", now)
    assert lineup.cost > 0 and lineup.captain == "Mid"
    with pytest.raises(GameError, match="joue"):
        fantasy.save_lineup(bob, week, {**picks, "Top": picks["Mid"]}, "Mid", now)
    with pytest.raises(GameError, match="plafond"):
        week.cap = 10
        fantasy.save_lineup(bob, week, picks, "Mid", now)
    week.cap = 1000
    alice = get_user_model().objects.create_user("alice", password="x" * 12)
    with pytest.raises(GameError, match="carte"):
        fantasy.save_lineup(alice, week, picks, "Mid", now)
    with pytest.raises(GameError, match="verrouillée"):
        fantasy.save_lineup(bob, week, picks, "Mid", paris(2027, 1, 13, 9))


def play(tournament, player_id, when, win, kills=3, team=None):
    return GameAppearance.objects.create(
        game_id=f"W{player_id}{when:%d%H}", tournament=tournament, player_id=player_id, team=team,
        role=Card.objects.filter(player_id=player_id).first().role, win=win, kills=kills, deaths=1, assists=5,
        cs=250, vision_score=25, game_length=28.0, played_at=when)


def test_a_week_is_scored_then_rewarded(lec, bob, split):
    Booster.objects.create(slug="base", name="Booster de base")
    comp = comp_for(paris(2027, 1, 5))
    week = fantasy.editable_week(comp, paris(2027, 1, 5))
    week.cap = 1000
    week.save()
    picks = lineup_ids(split)
    fantasy.save_lineup(bob, week, picks, "Mid", paris(2027, 1, 5))
    # Tous jouent une partie, sauf le Top : son remplaçant prend sa place.
    for role in ["Jungle", "Mid", "Bot", "Support"]:
        play(lec, picks[role], paris(2027, 1, 16, 18), win=True)
    play(lec, picks["sub"], paris(2027, 1, 17, 18), win=False, kills=8)
    fantasy.advance(comp, paris(2027, 1, 18))
    week.refresh_from_db()
    assert week.status == FantasyWeek.Status.LOCKED
    lineup = FantasyLineup.objects.get(user=bob, week=week)
    slots = {s["role"]: s for s in lineup.detail["slots"]}
    assert slots["Top"]["replaced"] and slots["Top"]["player"] == picks["sub"]
    mid = week.players.get(player_id=picks["Mid"])
    assert slots["Mid"]["points"] == pytest.approx(mid.points * 1.5, abs=0.1)
    assert mid.games[0]["base"] == fantasy.game_points(mid.games[0])[0]
    # Le mercredi suivant : comptage définitif, palier, booster, et
    # l'équipe est reconduite pour la semaine 2.
    fantasy.advance(comp, paris(2027, 1, 20, 1))
    week.refresh_from_db()
    lineup.refresh_from_db()
    assert week.status == FantasyWeek.Status.SCORED and week.ideal_score > 0
    assert lineup.tier in ("or", "argent", "bronze")
    assert BoosterGift.objects.filter(user=bob).exists()
    week2 = FantasyWeek.objects.get(competition=comp, number=2)
    assert FantasyLineup.objects.get(user=bob, week=week2).players == lineup.players


# --------------------------------------------------------------------- International fictif
@pytest.fixture
def offseason(split):
    """Le split LEC 2025 avec 12 parties par joueur (assez pour être simulé)."""
    tournament = Tournament.objects.get(split=split)
    for a in list(GameAppearance.objects.filter(tournament=tournament)):
        a.pk = None
        a.game_id = a.game_id + "b"
        a.played_at = paris(2025, 3, 1)
        a.save()
    return split


def test_the_fictional_international_simulates_real_games(offseason):
    now = paris(2026, 11, 23)
    comps = fantasy.sync_competitions(now)
    comp = next(c for c in comps if c.kind == "fictional")
    assert len(comp.state["teams"]) == 2
    week = fantasy.current_week(comp, now)
    assert fantasy_sim.simulate_week(week) == 2  # 2 rondes, 1 match chacune
    match = week.matches.first()
    assert max(match.wins_a, match.wins_b) == 2
    for game in match.games:
        for pid, aid in game["lines"].items():
            a = GameAppearance.objects.get(pk=aid)
            assert a.player_id == int(pid)
            won = (game["winner"] == "a") == (int(pid) in match.team_a["players"].values())
            assert a.win == won
    # Les points restent entre la pire et la meilleure vraie partie.
    fantasy.score_week(week, fantasy.week_end(week), final=True)
    for pw in week.players.exclude(games=[]):
        real = [fantasy.game_points(fantasy.appearance_line(a))[0]
                for a in GameAppearance.objects.filter(player=pw.player)]
        for g in pw.games:
            assert min(real) <= g["base"] <= max(real)
    # Le même tirage pour tout le monde.
    first = [(m.wins_a, m.wins_b, m.games) for m in week.matches.all()]
    week.matches.all().delete()
    comp.state["records"], comp.state["met"] = {}, []
    comp.save()
    fantasy_sim.simulate_week(week)
    assert [(m.wins_a, m.wins_b, m.games) for m in week.matches.all()] == first


def test_results_are_revealed_day_by_day(offseason):
    now = paris(2026, 11, 25, 12)  # mercredi midi
    comp = next(c for c in fantasy.sync_competitions(now) if c.kind == "fictional")
    week = fantasy.current_week(comp, now)
    fantasy_sim.simulate_week(week)
    assert fantasy.revealed_day(week, now) == date(2026, 11, 24)
    assert not fantasy.week_games(week, now)
    assert fantasy.revealed_day(week, paris(2026, 11, 25, 19)) == date(2026, 11, 25)


# --------------------------------------------------------------------- Pages
def test_the_pages(client, lec, bob, split):
    now = timezone.now()
    comp = FantasyCompetition.objects.create(kind="league", name="LEC test", slug="lec-test", league=split.league,
                                             start=fantasy.wednesday(fantasy.today(now)), end=fantasy.today(now)
                                             + timedelta(days=40))
    comp.tournaments.add(lec)
    assert client.get("/fantasy/").status_code == 302
    for vue in ("equipe", "semaine", "classement", "regles"):
        assert client.get(f"/fantasy/{comp.slug}/?vue={vue}").status_code == 200
    client.force_login(bob)
    page = client.get(f"/fantasy/{comp.slug}/")
    assert page.status_code == 200 and b"fy-form" in page.content
    week = fantasy.editable_week(comp)
    week.cap = 1000
    week.save()
    picks = lineup_ids(split)
    response = client.post(f"/fantasy/{comp.slug}/equipe/", {**picks, "captain": "Bot"})
    assert response.status_code == 302
    assert FantasyLineup.objects.get(user=bob, week=week).captain == "Bot"
    for vue in ("equipe", "semaine", "classement"):
        assert client.get(f"/fantasy/{comp.slug}/?vue={vue}").status_code == 200
