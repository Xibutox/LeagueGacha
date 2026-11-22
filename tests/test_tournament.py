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


def my_move(player, series, rng):
    """Un coup du joueur du site : le premier champion libre, au premier
    poste libre. Renvoie la partie si elle s'est jouée."""
    state = game.draft_state(series)
    free = next(c for c in Champion.objects.order_by("name").values_list("pk", flat=True) if c not in state["taken"])
    role = next((r for r in ROLES if r not in state["picks"]["user"]), None)
    return game.draft_action(player, series, free, role=role if state["turn"]["type"] == "pick" else None, rng=rng)


def play_one_game(player, series, rng):
    while True:
        played = my_move(player, series, rng)
        series.refresh_from_db()
        if played:
            return played


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


def test_a_bo5_with_a_turn_based_fearless_draft(tourney, player):
    series = game.start_series(player, tourney)
    assert series.status == TournamentSeries.Status.LINEUP and set(series.opponent) == set(ROLES)
    assert game.start_series(player, tourney) == series  # le Bo5 en cours

    with pytest.raises(GameError, match="collection"):
        game.set_lineup(player, series, {r: 999999 for r in ROLES})
    game.set_lineup(player, series, lineup_for(player))
    assert series.status == TournamentSeries.Status.DRAFT
    state = game.draft_state(series)
    assert state["side"] == "blue" and state["turn"] == {"team": "user", "type": "ban", "color": "blue", "index": 0}

    rng = random.Random(4)
    my_move(player, series, rng)  # ban bleu, puis l'adversaire bannit
    series.refresh_from_db()
    state = game.draft_state(series)
    assert len(state["bans"]["user"]) == 1 and len(state["bans"]["opponent"]) == 1
    with pytest.raises(GameError, match="banni ou pris"):
        game.draft_action(player, series, state["bans"]["opponent"][0], rng=rng)

    first = play_one_game(player, series, rng)
    series.refresh_from_db()
    assert first["number"] == 1 and len(first["bans"]["user"]) == len(first["bans"]["opponent"]) == 5
    assert len(set(first["user_picks"].values()) | set(first["opponent_picks"].values())) == 10
    assert abs(first["user"]["Top"]["bonus"] + first["opponent"]["Top"]["bonus"]) < 1e-9
    # Le perdant de la partie prend le côté bleu.
    assert game.draft_state(series)["side"] == ("red" if first["won"] else "blue")

    # Fearless : un champion joué en partie 1 ne revient plus, pour personne.
    if game.draft_state(series)["turn"]["team"] == "user":
        with pytest.raises(GameError, match="Fearless"):
            game.draft_action(player, series, first["opponent_picks"]["Top"], rng=rng)

    while not series.is_over:
        play_one_game(player, series, rng)
        played = [set(g["user_picks"].values()) | set(g["opponent_picks"].values()) for g in series.games]
        assert len(set().union(*played)) == 10 * len(series.games)
    assert 3 in (series.wins, series.losses) and len(series.games) <= 5

    run = series.run
    run.refresh_from_db()
    if series.status == TournamentSeries.Status.WON:
        assert run.step == 1 and series.reward_card_id in [int(c) for c in series.opponent.values()]
        assert UserCard.objects.filter(user=player, card=series.reward_card, rarity=series.reward_rarity).exists()
        with pytest.raises(GameError, match="attendre"):  # 1 h avant l'adversaire suivant
            game.start_series(player, tourney)
    else:
        assert run.step == 0 and series.reward_card is None
        assert game.run_status(run)["can_start"]  # le premier adversaire se retente tout de suite


def test_the_wait_follows_every_bo5_except_a_first_defeat(tourney, player):
    run = game.get_run(player, tourney)
    now = timezone.now()
    lost_first = TournamentSeries.objects.create(run=run, step=0, opponent={}, opponent_rarity=game.rarity_for_step(0, 2),
                                                 status=TournamentSeries.Status.LOST, finished_at=now)
    assert game.run_status(run, now)["can_start"]
    lost_first.status = TournamentSeries.Status.WON
    lost_first.save()
    run.step = 1
    run.save()
    status = game.run_status(run, now)
    assert not status["can_start"] and status["next_at"] == now + timedelta(hours=1)
    TournamentSeries.objects.create(run=run, step=1, opponent={}, opponent_rarity=game.rarity_for_step(1, 2),
                                    status=TournamentSeries.Status.LOST, finished_at=now + timedelta(minutes=5))
    assert not game.run_status(run, now + timedelta(minutes=10))["can_start"]


def test_only_the_player_whose_turn_it_is_acts(tourney, player):
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    rng = random.Random(1)
    first = play_one_game(player, series, rng)
    series.refresh_from_db()
    if first["won"]:  # côté rouge : l'adversaire a déjà ouvert la draft
        assert len(series.draft["actions"]) == 1 and series.draft["actions"][0]["team"] == "opponent"


def test_the_higher_score_wins_and_matchups_count(tourney, player, monkeypatch):
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    picks = free_picks(series)
    theirs = dict(zip(ROLES, [c for c in Champion.objects.values_list("pk", flat=True) if c not in picks.values()]))
    plain = game.evaluate_game(series, picks, theirs)
    assert plain["won"] == ((plain["user_score"], plain["user_coherence"])
                            > (plain["opponent_score"], plain["opponent_coherence"]))

    mine = {plain["user"][r]["champion_name"] for r in ROLES}
    real = game.draftstats.base_rate
    monkeypatch.setattr(game.draftstats, "base_rate",
                        lambda name, role, year, table=None: 0.7 if name in mine else real(name, role, year, table))
    favoured = game.evaluate_game(series, picks, theirs)
    assert favoured["user"]["Mid"]["bonus"] > plain["user"]["Mid"]["bonus"]
    assert favoured["user_score"] > plain["user_score"]
    assert favoured["user_draft"] > plain["user_draft"]


def test_the_draft_rating_comes_from_real_games(msi):
    result = game.draftstats.evaluate({"Top": "Ahri", "Mid": "Azir"}, {"Top": "Azir", "Mid": "Ahri"}, 2025)
    assert 0 < result["rate_a"] < 1
    assert result["a"]["Top"]["lane_games"] >= 0 and 0 < result["a"]["Top"]["lane_rate"] < 1


def test_the_opponent_picks_what_its_players_played(tourney, player):
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    played = set(Champion.objects.filter(name__in=["Ahri", "Azir", "Kai'Sa", "Wukong", "Lee Sin", "Nautilus", "Gnar"])
                 .values_list("pk", flat=True))
    state = game.draft_state(series)
    move = game.opponent_action(series, state, "pick", rng=random.Random(1))
    assert move["champion"] in played and move["role"] in ROLES


def test_the_bo5_page_loads_once_then_plays_in_json(client, tourney, player):
    import json

    client.force_login(player)
    page = client.get(reverse("cards:tournament"))
    assert page.status_code == 200 and "Défier" in page.content.decode()
    response = client.post(reverse("cards:tournament_start", args=[tourney.pk]))
    series = TournamentSeries.objects.get()
    assert response["Location"] == reverse("cards:tournament_series", args=[series.pk])
    html = client.get(response["Location"]).content.decode()
    assert 'id="bo5-state"' in html and 'id="bo5-static"' in html and "tournament.js" in html

    url = reverse("cards:tournament_action", args=[series.pk])

    def act(**payload):
        return client.post(url, json.dumps(payload), content_type="application/json")

    refused = act(type="lineup", picks={r: 999999 for r in ROLES})
    assert refused.status_code == 409 and "collection" in refused.json()["error"]
    done = act(type="lineup", picks=lineup_for(player))
    assert done.status_code == 200
    data = done.json()
    assert data["state"]["status"] == "draft" and data["state"]["draft"]["turn"]["type"] == "ban"
    assert set(data["static"]["ratings"]) == set(ROLES) and data["static"]["champions"]

    rng_free = lambda st: next(c["id"] for c in data["static"]["champions"] if c["id"] not in st["draft"]["taken"])
    state = data["state"]
    wrong = act(type="pick", champion=rng_free(state), role="Top")
    assert wrong.status_code == 409
    games_played = 0
    for _ in range(60):
        turn = state["draft"]["turn"]
        payload = {"type": turn["type"], "champion": rng_free(state)}
        if turn["type"] == "pick":
            payload["role"] = next(r for r in ROLES if r not in state["draft"]["picks"]["user"])
        reply = act(**payload)
        assert reply.status_code == 200, reply.content
        state = reply.json()["state"]
        if reply.json()["played"]:
            games_played += 1
            break
    assert games_played == 1 and state["games"][0]["number"] == 1 and len(state["games"][0]["rows"]) == 5
    assert "Reprendre ton Bo5" in client.get(reverse("cards:tournament")).content.decode()


def test_a_draft_action_runs_few_queries(client, tourney, player, django_assert_max_num_queries):
    import json

    client.force_login(player)
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    series.refresh_from_db()
    url = reverse("cards:tournament_action", args=[series.pk])
    state = game.draft_state(series)
    free = next(c for c in Champion.objects.values_list("pk", flat=True) if c not in state["taken"])
    client.get(reverse("cards:tournament"))  # caches chauds
    with django_assert_max_num_queries(40):
        reply = client.post(url, json.dumps({"type": "ban", "champion": free}), content_type="application/json")
    assert reply.status_code == 200


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


def test_the_auto_mode_bans_their_favourite_and_picks_my_best(tourney, player):
    from cards.models import CardChampion

    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    series.refresh_from_db()
    state = game.draft_state(series)
    pools = game.prepare(series)["pools"]
    counts = {}
    for pool in pools.values():
        for cid, n in pool:
            counts[cid] = counts.get(cid, 0) + n
    assert game.auto_move(series, state)["champion"] == max(counts, key=counts.get)

    rng = random.Random(3)
    for _ in range(12):
        series.refresh_from_db()
        state = game.draft_state(series)
        if state["turn"]["type"] == "pick":
            break
        game.draft_action(player, series, "auto", rng=rng)
    move = game.auto_move(series, state)
    card = int(series.lineup[move["role"]])
    best = (CardChampion.objects.filter(card_id__in=[int(c) for c in series.lineup.values()])
            .exclude(champion_id__in=state["taken"]).order_by("-rating").first())
    assert CardChampion.objects.get(card_id=card, champion_id=move["champion"]).rating == best.rating


def test_each_game_has_a_special_rule_for_both_teams(tourney, player):
    series = game.start_series(player, tourney)
    game.set_lineup(player, series, lineup_for(player))
    series.refresh_from_db()
    special = series.draft["special"]
    assert special["year"] == tourney.split.year
    for team in ("user", "opponent"):
        side = special[team]
        assert side["role"] in ROLES and 1 <= len(side["effects"]) <= 3
        effect = side["effects"][0]
        assert -8 <= effect["points"] <= 8 and 0 < effect["rate"] < 100
        assert game.special_points(special, team, side["role"], effect["champion"]) == effect["points"]
        other = next(r for r in ROLES if r != side["role"])
        assert game.special_points(special, team, other, effect["champion"]) == 0
    # L'adversaire : ses préférés sont ce que son joueur a joué pendant le tournoi.
    pool = [cid for cid, _ in game.prepare(series)["pools"][special["opponent"]["role"]]]
    assert all(e["champion"] in pool for e in special["opponent"]["effects"])

    played = play_one_game(player, series, random.Random(5))
    assert played["special"] == special and len(played["actions"]) == 20
    for team in ("user", "opponent"):
        slot = played[team][special[team]["role"]]
        assert slot["score"] == round(slot["base"] + slot["bonus"] + slot["special"], 1)
