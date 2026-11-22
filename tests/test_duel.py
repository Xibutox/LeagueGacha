"""Le 1V1 : un Bo5 entre deux joueurs du site, avec une méta par duel."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from cards import duel as game
from cards.models import ROLES, Card, Duel
from cards.services import GameError
from esport.models import Champion

from .test_game import bob, give  # noqa: F401


@pytest.fixture
def players(split, bob):  # noqa: F811
    alice = get_user_model().objects.create_user("alice", password="mdp-alice-123")
    for card in Card.objects.all():
        give(bob, card)
        give(alice, card)
    for n in range(60):
        Champion.objects.get_or_create(name=f"Champion {n:02d}", defaults={"key": f"Champion{n:02d}"})
    return bob, alice


def lineup(team):
    return {c.role: c.pk for c in Card.objects.filter(team__name=team)}


def started(players):
    bob, alice = players
    duel = game.invite(bob, alice)
    duel = game.respond(alice, duel, True)
    game.set_lineup(bob, duel, lineup("G2 Esports"))
    return game.set_lineup(alice, Duel.objects.get(pk=duel.pk), lineup("Fnatic"))


def move(duel, user):
    """Le premier champion libre au premier poste libre, pour celui dont c'est le tour."""
    duel.refresh_from_db()
    state = game.draft_state(duel)
    side = duel.side_of(user)
    free = next(c for c in Champion.objects.order_by("name").values_list("pk", flat=True) if c not in state["taken"])
    role = next((r for r in ROLES if r not in state["picks"][side]), None) if state["turn"]["type"] == "pick" else None
    return game.play(user, duel, state["turn"]["type"], free, role)


def whose_turn(duel, players):
    duel.refresh_from_db()
    turn = game.draft_state(duel)["turn"]
    return players[0] if turn["team"] == "a" else players[1]


def test_an_invitation_must_be_accepted(players):
    bob, alice = players
    duel = game.invite(bob, alice)
    assert duel.status == Duel.Status.INVITED
    with pytest.raises(GameError, match="déjà en cours"):
        game.invite(alice, bob)
    with pytest.raises(GameError):
        game.invite(bob, bob)
    duel = game.respond(alice, duel, True)
    assert duel.status == Duel.Status.LINEUP
    assert set(duel.meta) == set(ROLES) and len({r["kind"] for r in duel.meta.values()}) == 5
    other = game.invite(bob, get_user_model().objects.create_user("carol", password="x" * 12))
    game.respond(other.opponent, other, False)
    assert Duel.objects.get(pk=other.pk).status == Duel.Status.DECLINED


def test_a_full_bo5_between_two_players(players):
    duel = started(players)
    assert duel.status == Duel.Status.DRAFT
    assert duel.draft["blue"] == "a"
    with pytest.raises(GameError, match="pas ton tour"):
        move(duel, players[1])
    for _ in range(200):
        duel.refresh_from_db()
        if duel.status == Duel.Status.FINISHED:
            break
        move(duel, whose_turn(duel, players))
    assert duel.status == Duel.Status.FINISHED
    assert max(duel.wins_a, duel.wins_b) == 3
    assert duel.winner in players
    # Fearless : aucun champion joué deux fois dans le Bo5.
    picks = [c for g in duel.games for side in ("a", "b") for c in g[f"picks_{side}"].values()]
    assert len(picks) == len(set(picks))
    # Le perdant d'une partie prend le côté bleu à la suivante.
    for previous, game_ in zip(duel.games, duel.games[1:]):
        assert game_["blue"] != previous["winner"]
    # Les scores comptent la méta du duel.
    assert all("meta" in g["a"][r] for g in duel.games for r in ROLES)


def test_a_missed_turn_is_played_automatically_then_auto_mode(players):
    duel = started(players)
    now = timezone.now()
    for i in range(3):
        duel.refresh_from_db()
        turn_side = game.draft_state(duel)["turn"]["team"]
        duel.deadline = now - timedelta(seconds=1)
        duel.save()
        before = len(duel.draft["actions"])
        duel = game.refresh(duel, now)
        assert len(duel.draft["actions"]) > before
        assert getattr(duel, f"missed_{turn_side}") >= 1
        if getattr(duel, f"missed_{turn_side}") >= game.AUTO_AFTER_MISSES:
            assert getattr(duel, f"auto_{turn_side}")
    # Une invitation trop vieille expire.
    bob, alice = players
    carol = get_user_model().objects.create_user("carol", password="x" * 12)
    old = game.invite(alice, carol)
    Duel.objects.filter(pk=old.pk).update(created_at=now - timedelta(hours=game.INVITE_HOURS + 1))
    assert game.refresh(Duel.objects.get(pk=old.pk)).status == Duel.Status.EXPIRED


def test_the_lineup_is_chosen_automatically_after_the_delay(players):
    bob, alice = players
    duel = game.respond(alice, game.invite(bob, alice), True)
    game.set_lineup(bob, duel, lineup("G2 Esports"))
    Duel.objects.filter(pk=duel.pk).update(deadline=timezone.now() - timedelta(seconds=1))
    duel = game.refresh(Duel.objects.get(pk=duel.pk))
    assert duel.status == Duel.Status.DRAFT and len(duel.lineup_b) == 5


def test_forfeit_gives_the_win(players):
    duel = started(players)
    duel = game.forfeit(players[0], duel)
    assert duel.status == Duel.Status.FINISHED and duel.winner == players[1] and duel.forfeit


def test_duel_pages_and_api(client, players):
    bob, alice = players
    client.force_login(bob)
    assert client.get(reverse("cards:duels")).status_code == 200
    response = client.post(reverse("cards:duel_invite"), {"username": "ALICE"})
    assert response.status_code == 302
    duel = Duel.objects.get(challenger=bob)
    client.force_login(alice)
    assert "te défie" in client.get(reverse("cards:duels")).content.decode()
    response = client.post(reverse("cards:duel_respond", args=[duel.pk]), {"answer": "accept"})
    assert response.url == reverse("cards:duel", args=[duel.pk])
    page = client.get(reverse("cards:duel", args=[duel.pk]))
    assert page.status_code == 200 and "bo5-state" in page.content.decode()
    answer = client.post(reverse("cards:duel_action", args=[duel.pk]), {"type": "lineup", "picks": lineup("Fnatic")},
                         content_type="application/json").json()
    assert answer["state"]["lineup_done"] and not answer["state"]["opponent_ready"]
    assert answer["state"]["opponent"] == [None] * 5  # caché tant que l'autre n'a pas choisi
    client.force_login(bob)
    client.post(reverse("cards:duel_action", args=[duel.pk]), {"type": "lineup", "picks": lineup("G2 Esports")},
                content_type="application/json")
    state = client.get(reverse("cards:duel_state", args=[duel.pk]) + "?static=1").json()
    assert state["state"]["status"] == "draft" and state["state"]["draft"]["turn"]["team"] == "user"
    assert state["static"]["ratings"] and "metaChampions" in state["static"]
    played = client.post(reverse("cards:duel_action", args=[duel.pk]), {"type": "ban", "auto": True},
                         content_type="application/json")
    assert played.status_code == 200
    # Un tiers ne voit pas le duel.
    client.force_login(get_user_model().objects.create_user("eve", password="x" * 12))
    assert client.get(reverse("cards:duel", args=[duel.pk])).status_code == 404
