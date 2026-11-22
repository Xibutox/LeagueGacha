"""Le mode tournoi : rejouer un tournoi international en Bo5 Fearless."""

import random
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from cards import tournament as game
from cards.models import ROLES, Card, Tournament, TournamentSeries, UserCard
from cards.services import GameError
from esport.models import Champion, League
from tests.test_game import bob, give  # noqa: F401


@pytest.fixture
def msi(split):
    """Le split d'essai devient un MSI (deux équipes complètes)."""
    League.objects.filter(pk=split.league_id).update(
        name="Mid-Season Invitational", short_name="MSI", region="International"
    )
    for n in range(60):
        Champion.objects.get_or_create(name=f"Champion {n:02d}", defaults={"key": f"Champion{n:02d}"})
    return split


@pytest.fixture
def tourney(msi):
    return game.ensure_tournament()


@pytest.fixture
def player(bob, msi):
    for card in Card.objects.all():
        give(bob, card)
    return bob


def lineup_for(user):
    return {c.role: c.pk for c in Card.objects.filter(team__name="G2 Esports")}


def free_picks(series):
    used = series.used_champions()
    free = [c for c in Champion.objects.order_by("name").values_list("pk", flat=True) if c not in used]
    return dict(zip(ROLES, free))


def test_the_automatic_tournament_replays_an_international_event(tourney, msi):
    assert tourney.auto and tourney.split == msi
    assert tourney.ends_at - tourney.starts_at == timedelta(days=7)
    assert [o["name"] for o in tourney.opponents] and len(tourney.opponents) == 2
    assert game.ensure_tournament() == tourney  # un seul à la fois


def test_no_tournament_without_an_international_event(split):
    assert game.ensure_tournament() is None


def test_opponent_cards_get_rarer_and_rewards_too(tourney):
    first, last = game.rarity_for_step(0, 8), game.rarity_for_step(7, 8)
    assert first.code == "commune" and last.code == "legendaire"
    assert game.reward_rarity(2, 8).code == "commune" and game.reward_rarity(5, 8).code == "rare"


def test_a_bo5_with_a_fearless_blind_draft(tourney, player):
    series = game.start_series(player, tourney)
    assert series.status == TournamentSeries.Status.LINEUP and set(series.opponent) == set(ROLES)
    assert game.start_series(player, tourney) == series  # le Bo5 en cours

    with pytest.raises(GameError, match="collection"):
        game.set_lineup(player, series, {r: 999999 for r in ROLES})
    game.set_lineup(player, series, lineup_for(player))
    assert series.status == TournamentSeries.Status.DRAFT

    rng = random.Random(4)
    first = game.play_game(player, series, free_picks(series), rng=rng)
    series.refresh_from_db()
    assert first["number"] == 1 and len(first["opponent"]) == 5
    assert {"score", "matchup", "bonus"} <= set(first["user"]["Top"])
    assert abs(first["user"]["Top"]["bonus"] + first["opponent"]["Top"]["bonus"]) < 1e-9

    # Fearless : aucun champion de la partie 1 ne revient, pour personne.
    reused = dict(free_picks(series), Top=first["opponent_picks"]["Top"])
    with pytest.raises(GameError, match="Fearless"):
        game.play_game(player, series, reused, rng=rng)

    while not series.is_over:
        game.play_game(player, series, free_picks(series), rng=rng)
        series.refresh_from_db()
        played = [set(g["user_picks"].values()) | set(g["opponent_picks"].values()) for g in series.games]
        # Aucun champion dans deux parties différentes (à l'aveugle, un miroir
        # dans la même partie reste possible).
        assert len(set().union(*played)) == sum(len(p) for p in played)
    assert 3 in (series.wins, series.losses) and len(series.games) <= 5

    run = series.run
    run.refresh_from_db()
    if series.status == TournamentSeries.Status.WON:
        assert run.step == 1 and series.reward_card_id in [int(c) for c in series.opponent.values()]
        assert UserCard.objects.filter(user=player, card=series.reward_card, rarity=series.reward_rarity).exists()
    else:
        assert run.step == 0 and series.reward_card is None
    # Une heure d'attente avant le Bo5 suivant.
    with pytest.raises(GameError, match="attendre"):
        game.start_series(player, tourney)
    later = series.finished_at + timedelta(hours=1, minutes=1)
    assert game.run_status(run, later)["can_start"] or run.finished


def test_the_higher_score_wins_and_matchups_count(tourney, player, monkeypatch):
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    picks = free_picks(series)
    theirs = dict(zip(ROLES, [c for c in Champion.objects.values_list("pk", flat=True) if c not in picks.values()]))
    plain = game.evaluate_game(series, picks, theirs)
    assert plain["won"] == ((plain["user_score"], plain["user_coherence"])
                            > (plain["opponent_score"], plain["opponent_coherence"]))

    monkeypatch.setattr(game, "matchup", lambda role, a, b: {"games": 50, "wins": 50, "rate": 0.9})
    favoured = game.evaluate_game(series, picks, theirs)
    assert favoured["user"]["Mid"]["bonus"] == 16.0
    assert favoured["user_score"] > plain["user_score"] and favoured["opponent_score"] < plain["opponent_score"]


def test_the_matchup_comes_from_real_games(msi):
    face = game.matchup("Top", "Ahri", "Azir")
    assert 0 <= face["games"] and 0 < face["rate"] < 1
    assert game.matchup("Top", "Ahri", "Ahri")["rate"] == 0.5


def test_the_opponent_drafts_what_its_players_played(tourney, player):
    series = game.start_series(player, tourney)
    picks = game.opponent_draft(series, banned=set(), rng=random.Random(1))
    played = set(Champion.objects.filter(name__in=["Ahri", "Azir", "Kai'Sa", "Wukong", "Lee Sin", "Nautilus", "Gnar"])
                 .values_list("pk", flat=True))
    assert len(set(picks.values())) == 5 and set(picks.values()) <= played


def test_the_tournament_pages(client, tourney, player):
    client.force_login(player)
    page = client.get(reverse("cards:tournament"))
    assert page.status_code == 200 and "Défier" in page.content.decode()
    response = client.post(reverse("cards:tournament_start", args=[tourney.pk]))
    series = TournamentSeries.objects.get()
    assert response["Location"] == reverse("cards:tournament_series", args=[series.pk])
    assert "Choisis tes 5 joueurs" in client.get(response["Location"]).content.decode()

    client.post(reverse("cards:tournament_lineup", args=[series.pk]), lineup_for(player))
    assert "ta draft" in client.get(response["Location"]).content.decode()
    series.refresh_from_db()
    played = client.post(reverse("cards:tournament_draft", args=[series.pk]), free_picks(series))
    assert "#partie-1" in played["Location"]
    html = client.get(response["Location"]).content.decode()
    assert "Partie 1 :" in html and "Fearless, déjà joués" in html
    assert "Reprendre ton Bo5" in client.get(reverse("cards:tournament")).content.decode()


def test_someone_elses_bo5_is_hidden(client, tourney, player, django_user_model):
    series = game.start_series(player, tourney)
    other = django_user_model.objects.create_user("eve", password="mdp-eve-12345")
    client.force_login(other)
    assert client.get(reverse("cards:tournament_series", args=[series.pk])).status_code == 404


def test_a_closed_tournament_cannot_be_played(tourney, player):
    Tournament.objects.filter(pk=tourney.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
    tourney.refresh_from_db()
    with pytest.raises(GameError, match="fermé"):
        game.start_series(player, tourney)
