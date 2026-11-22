import pytest
from django.core.cache import cache

from cards import draftstats
from esport.models import GameAppearance, League, Player, Split, Tournament

ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]


def test_logit_round_trip():
    for p in (0.1, 0.35, 0.5, 0.62, 0.9):
        assert draftstats.W(draftstats.L(p)) == pytest.approx(p)
    assert draftstats.L(0.5) == pytest.approx(0)
    assert draftstats.W(400) == pytest.approx(10 / 11)


@pytest.fixture
def games(db):
    """30 parties : Aatrox (Top, bleu) gagne 24 fois contre Gnar."""
    cache.clear()
    league = League.objects.create(name="LCK")
    split = Split.objects.create(league=league, year=2026, name="LCK 2026 Spring", slug="lck-2026-spring")
    tournament = Tournament.objects.create(league=league, split=split, year=2026, name="LCK 2026 Spring", overview_page="x")
    players = [Player.objects.create(ign=f"p{i}", overview_page=f"p{i}") for i in range(10)]
    blue = ["Aatrox", "Vi", "Ahri", "Jinx", "Thresh"]
    red = ["Gnar", "Sejuani", "Orianna", "Ezreal", "Braum"]
    rows = []
    for g in range(30):
        blue_wins = g < 24
        for i, role in enumerate(ROLES):
            rows.append(GameAppearance(game_id=f"g{g}", tournament=tournament, player=players[i], role=role,
                                       champion=blue[i], win=blue_wins))
            rows.append(GameAppearance(game_id=f"g{g}", tournament=tournament, player=players[5 + i], role=role,
                                       champion=red[i], win=not blue_wins))
    GameAppearance.objects.bulk_create(rows)
    yield dict(zip(ROLES, blue)), dict(zip(ROLES, red))
    cache.clear()


def test_strong_draft_is_favoured(games):
    blue, red = games
    result = draftstats.evaluate(blue, red, 2026)
    assert result["rate_a"] > 0.6
    assert result["rate_a"] + result["rate_b"] == pytest.approx(1)
    top = result["a"]["Top"]
    assert top["lane_games"] == 30
    assert top["lane_rate"] > 0.5
    assert draftstats.slot_points(top) > 0 > draftstats.slot_points(result["b"]["Top"])


def test_unknown_champions_are_neutral(games):
    result = draftstats.evaluate({"Top": "Nobody"}, {"Top": "Nemo"}, 2026)
    assert result["rate_a"] == pytest.approx(0.5)
    assert draftstats.slot_points(result["a"]["Top"]) == 0
