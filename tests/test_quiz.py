"""Le quiz : 3 questions toutes les 4 heures."""

import random
from datetime import datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from cards import quiz
from cards.models import QuizAttempt, UserCard
from cards.services import GameError
from esport.models import GameAppearance, League, Team, Tournament
from tests.test_game import bob  # noqa: F401


@pytest.fixture
def history(split, monkeypatch):
    """Le split d'essai devient un MSI daté, avec une troisième équipe et un
    joueur passé par trois équipes."""
    League.objects.filter(pk=split.league_id).update(
        name="Mid-Season Invitational", short_name="MSI", region="International"
    )
    start = timezone.make_aware(datetime(2025, 5, 1, 12))
    for a in GameAppearance.objects.all():
        a.played_at = start + timedelta(hours=int(a.game_id[1:]))
        a.save(update_fields=["played_at"])
    t1 = Team.objects.create(name="T1")
    old = Tournament.objects.create(overview_page="Old", name="Old Cup", league=split.league, split=split, year=2025)
    player = GameAppearance.objects.filter(team__name="G2 Esports").first().player
    for n, team in enumerate([t1, t1, Team.objects.get(name="Fnatic")]):
        GameAppearance.objects.create(game_id=f"OLD{n}", tournament=old, player=player, team=team, role="Top",
                                      champion="Ahri", win=True, kills=1, deaths=1, assists=1,
                                      played_at=start - timedelta(days=30 - n))
    monkeypatch.setattr(quiz, "CAREER_MIN_GAMES", 5)
    final = GameAppearance.objects.filter(win=True, team__isnull=False).order_by("-played_at").first()
    return {"winner": str(final.team_id), "player": player, "g2": Team.objects.get(name="G2 Esports")}


def test_the_three_questions(history):
    rng = random.Random(1)
    winner = quiz.winner_question(rng)
    assert winner and winner["answer"] in ([history["winner"]], [str(winner["answer"][0])])
    assert winner["attempts"] == 2 and len(winner["options"]) >= 3
    roster = quiz.roster_question(rng)
    assert roster["pick"] == 5 and len(roster["answer"]) == 5
    assert set(roster["answer"]) <= {o["id"] for o in roster["options"]}
    career = quiz.career_question(rng)
    assert career["answer"] == [str(history["g2"].pk)]  # 6 parties chez G2, 2 chez T1, 1 chez Fnatic
    assert str(history["player"].ign) in career["text"]


def test_one_quiz_per_four_hours(history, bob):
    now = timezone.make_aware(datetime(2026, 11, 22, 13, 30))
    attempt = quiz.start(bob, now=now, rng=random.Random(2))
    assert attempt.period_start.hour == 12 and quiz.next_period(now).hour == 16
    assert quiz.start(bob, now=now + timedelta(hours=2)) == attempt
    assert quiz.start(bob, now=now + timedelta(hours=3), rng=random.Random(3)) != attempt


def wrong(question):
    return [o["id"] for o in question["options"] if o["id"] not in question["answer"]][: question["pick"]]


def test_a_perfect_quiz_offers_five_rares(history, bob):
    attempt = quiz.start(bob, rng=random.Random(4))
    first = quiz.answer(bob, attempt, wrong(attempt.questions[0]))
    assert first["result"] is None and first["attempts"] == 1  # une erreur permise
    attempt.refresh_from_db()
    quiz.answer(bob, attempt, attempt.questions[0]["answer"])
    for _ in range(2):
        attempt.refresh_from_db()
        quiz.answer(bob, attempt, attempt.questions[attempt.current]["answer"])
    attempt.refresh_from_db()
    assert attempt.score == 3 and attempt.finished_at and attempt.reward_rarity.code == "rare"
    choices = list(attempt.reward_choices.all())
    assert len(choices) == min(5, len({c.player_id for c in choices}))
    card = quiz.choose(bob, attempt, choices[0].pk)
    assert UserCard.objects.filter(user=bob, card=card, rarity__code="rare").exists()
    with pytest.raises(GameError, match="déjà"):
        quiz.choose(bob, attempt, choices[1].pk)


def test_wrong_answers_still_lead_to_a_reward(history, bob):
    attempt = quiz.start(bob, rng=random.Random(5))
    quiz.answer(bob, attempt, wrong(attempt.questions[0]))
    attempt.refresh_from_db()
    quiz.answer(bob, attempt, wrong(attempt.questions[0]))  # deux essais ratés
    attempt.refresh_from_db()
    assert attempt.questions[0]["result"] is False and attempt.current == 1  # on continue
    quiz.answer(bob, attempt, attempt.questions[1]["answer"])
    attempt.refresh_from_db()
    quiz.answer(bob, attempt, wrong(attempt.questions[2]))
    attempt.refresh_from_db()
    assert attempt.score == 1 and attempt.reward_rarity.code == "commune"
    assert attempt.reward_choices.count() == 3 and attempt.chosen is None


def test_zero_right_answers_gives_one_common_directly(history, bob):
    attempt = quiz.start(bob, rng=random.Random(6))
    for _ in range(4):
        attempt.refresh_from_db()
        if attempt.current is None:
            break
        quiz.answer(bob, attempt, wrong(attempt.questions[attempt.current]))
    attempt.refresh_from_db()
    assert attempt.score == 0 and attempt.chosen is not None
    assert UserCard.objects.filter(user=bob, card=attempt.chosen, rarity__code="commune").exists()


def test_a_roster_answer_needs_five_players(history, bob):
    attempt = quiz.start(bob, rng=random.Random(7))
    attempt.questions[0]["result"] = True
    attempt.save()
    with pytest.raises(GameError, match="5"):
        quiz.answer(bob, attempt, attempt.questions[1]["answer"][:3])


def test_the_quiz_page(client, history, bob):
    client.force_login(bob)
    assert "Commencer le quiz" in client.get(reverse("cards:quiz")).content.decode()
    client.post(reverse("cards:quiz_start"))
    attempt = QuizAttempt.objects.get()
    page = client.get(reverse("cards:quiz")).content.decode()
    assert attempt.questions[0]["text"] in page and "Question 1 sur 3" in page
    client.post(reverse("cards:quiz_answer", args=[attempt.pk]), {"choix": wrong(attempt.questions[0])})
    assert "il te reste un essai" in client.get(reverse("cards:quiz")).content.decode()
