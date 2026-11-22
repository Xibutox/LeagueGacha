"""Le quiz : 3 questions toutes les 4 heures, tirées des données importées.

1. Le vainqueur : qui a remporté un tournoi (playoffs d'une ligue majeure,
   ou compétition internationale) ? Deux essais.
2. Le roster : les 5 joueurs d'une équipe arrivée dans le top 3 d'une
   compétition internationale de Riot (top 4 pour les Worlds ; EWC exclu).
3. La carrière : dans quelle équipe ce joueur a-t-il joué le plus de parties ?

Une mauvaise réponse ne coupe pas le quiz. À la fin : 3 bonnes réponses →
une rare au choix parmi 5 ; 1 ou 2 → une commune au choix parmi 3 ; 0 → une
commune au hasard.
"""

from __future__ import annotations

import random
from datetime import datetime, time as dtime, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from esport.catalog import major_leagues_q, riot_events_q, side_events_q, ewc_q
from esport.models import GameAppearance, League, Player, Split, Team, Tournament

from .models import ROLES, Card, QuizAttempt, Rarity
from .services import GameError, add_copies, refresh_rosters_of_user

PERIOD_HOURS = 4
WINNER_ATTEMPTS = 2
WINNER_OPTIONS = 6
ROSTER_DECOYS = 7
CAREER_OPTIONS = 5
CAREER_MIN_TEAMS = 3
CAREER_MIN_GAMES = 60
REWARDS = {3: (5, 1), 2: (3, 0), 1: (3, 0), 0: (1, 0)}  # bonnes réponses → (cartes proposées, rang de rareté)


# --------------------------------------------------------------- La période
def period_start(now=None):
    """Le début de la période de 4 h en cours (0 h, 4 h, 8 h… heure du site)."""
    local = timezone.localtime(now or timezone.now())
    start = datetime.combine(local.date(), dtime(hour=local.hour - local.hour % PERIOD_HOURS))
    return timezone.make_aware(start)


def next_period(now=None):
    return period_start(now) + timedelta(hours=PERIOD_HOURS)


def current_attempt(user, now=None) -> QuizAttempt | None:
    return QuizAttempt.objects.filter(user=user, period_start=period_start(now)).first()


# --------------------------------------------------------------- Questions
def _team_option(team_id: int, names: dict) -> dict:
    return {"id": str(team_id), "label": names.get(team_id, "?")}


def winner_question(rng) -> dict | None:
    """Qui a remporté… ? Des playoffs d'une ligue majeure, ou une compétition
    internationale (le vainqueur de sa dernière partie)."""
    key = f"quiz:winners:{Tournament.objects.count()}:{Split.objects.count()}"
    pool = cache.get(key)
    if pool is None:
        pool = _winner_pool()
        cache.set(key, pool, 6 * 3600)
    pool = list(pool)
    rng.shuffle(pool)
    for kind, pk in pool[:12]:
        found = _winner_of(kind, pk)
        if found is None:
            continue
        title, games, final = found
        teams = list(games.exclude(team__isnull=True).values_list("team", flat=True).distinct())
        if len(teams) < 3:
            continue
        others = [t for t in teams if t != final.team_id]
        rng.shuffle(others)
        options = [final.team_id] + others[: WINNER_OPTIONS - 1]
        rng.shuffle(options)
        names = dict(Team.objects.filter(pk__in=options).values_list("pk", "name"))
        return {
            "kind": "winner",
            "title": "Le vainqueur",
            "text": f"Qui a remporté {title} ?",
            "options": [_team_option(t, names) for t in options],
            "answer": [str(final.team_id)],
            "pick": 1,
            "attempts": WINNER_ATTEMPTS,
            "picked": [],
            "result": None,
        }
    return None


def _winner_pool() -> list[tuple[str, int]]:
    majors = League.objects.filter(major_leagues_q())
    events = League.objects.filter(riot_events_q() | ewc_q())
    playoffs = list(
        Tournament.objects.filter(league__in=majors, is_playoffs=True, is_qualifier=False)
        .exclude(side_events_q()).filter(appearances__played_at__isnull=False).distinct()
        .values_list("pk", flat=True)
    )
    internationals = list(
        Split.objects.filter(league__in=events, is_playable=True).exclude(side_events_q())
        .filter(tournaments__appearances__played_at__isnull=False).distinct().values_list("pk", flat=True)
    )
    return [("t", pk) for pk in playoffs] + [("s", pk) for pk in internationals]


def _winner_of(kind: str, pk: int):
    if kind == "t":
        tournament = Tournament.objects.get(pk=pk)
        games, title = GameAppearance.objects.filter(tournament=tournament), tournament.name
    else:
        split = Split.objects.get(pk=pk)
        games, title = GameAppearance.objects.filter(tournament__split=split), split.name
    final = games.filter(win=True, team__isnull=False, played_at__isnull=False).order_by("-played_at").first()
    return (title, games, final) if final else None


def roster_question(rng) -> dict | None:
    """Les 5 joueurs d'une équipe du top 3 (top 4 aux Worlds) d'une
    compétition internationale de Riot."""
    from .tournament import opponent_cards, standings

    splits = list(
        Split.objects.filter(league__in=League.objects.filter(riot_events_q()), is_playable=True,
                             cards__isnull=False).exclude(side_events_q()).distinct()
    )
    rng.shuffle(splits)
    for split in splits[:8]:
        worlds = (split.league.short_name or "").upper() in {"WCS", "WORLDS"} or "world" in split.league.name.lower()
        top = 4 if worlds else 3
        ranking = standings(split)
        if len(ranking) < 2:
            continue
        podium = ranking[-top:]
        team_id = rng.choice(podium)
        place = len(ranking) - ranking.index(team_id)
        lineup = opponent_cards(split, team_id)
        if len(lineup) < len(ROLES):
            continue
        cards = Card.objects.select_related("player").in_bulk(list(lineup.values()))
        answer = [cards[lineup[r]] for r in ROLES]
        decoys = list(
            Card.objects.filter(split=split, is_active=True).exclude(team_id=team_id)
            .exclude(player__in=[c.player_id for c in answer]).select_related("player")
        )
        rng.shuffle(decoys)
        picked_players = {c.player_id for c in answer}
        chosen_decoys = []
        for c in decoys:
            if c.player_id not in picked_players:
                picked_players.add(c.player_id)
                chosen_decoys.append(c)
            if len(chosen_decoys) >= ROSTER_DECOYS:
                break
        options = answer + chosen_decoys
        rng.shuffle(options)
        team = Team.objects.get(pk=team_id)
        label = {1: "vainqueur", 2: "finaliste"}.get(place, "demi-finaliste" if worlds else "3e")
        return {
            "kind": "roster",
            "title": "Le roster",
            "text": f"Retrouve les 5 joueurs de {team.name}, {label} de {split.name}.",
            "options": [{"id": str(c.player_id), "label": c.player.ign, "sub": c.role} for c in options],
            "answer": [str(c.player_id) for c in answer],
            "pick": len(ROLES),
            "attempts": 1,
            "picked": [],
            "result": None,
        }
    return None


def career_question(rng) -> dict | None:
    """Dans quelle équipe ce joueur a-t-il joué le plus de parties ?"""
    key = f"quiz:career:{CAREER_MIN_TEAMS}:{CAREER_MIN_GAMES}:{GameAppearance.objects.count()}"
    candidates = cache.get(key)
    if candidates is None:  # la requête parcourt toutes les parties : gardée 6 h
        candidates = list(
            GameAppearance.objects.filter(team__isnull=False).values("player")
            .annotate(teams=Count("team", distinct=True), games=Count("id"))
            .filter(teams__gte=CAREER_MIN_TEAMS, games__gte=CAREER_MIN_GAMES).values_list("player", flat=True)[:3000]
        )
        cache.set(key, candidates, 6 * 3600)
    candidates = list(candidates)
    rng.shuffle(candidates)
    for player_id in candidates[:10]:
        rows = list(
            GameAppearance.objects.filter(player=player_id, team__isnull=False)
            .values("team").annotate(n=Count("id")).order_by("-n")
        )
        if len(rows) < 2 or rows[0]["n"] == rows[1]["n"]:
            continue  # pas d'équipe principale nette
        teams = [r["team"] for r in rows[:CAREER_OPTIONS]]
        if len(teams) < CAREER_OPTIONS:
            popular = (
                GameAppearance.objects.filter(team__isnull=False).exclude(team__in=teams)
                .values("team").annotate(n=Count("id")).order_by("-n").values_list("team", flat=True)[:30]
            )
            extra = list(popular)
            rng.shuffle(extra)
            teams += extra[: CAREER_OPTIONS - len(teams)]
        rng.shuffle(teams)
        names = dict(Team.objects.filter(pk__in=teams).values_list("pk", "name"))
        player = Player.objects.get(pk=player_id)
        return {
            "kind": "career",
            "title": "La carrière",
            "text": f"Dans quelle équipe {player.ign} a-t-il joué le plus de parties ?",
            "options": [_team_option(t, names) for t in teams],
            "answer": [str(rows[0]["team"])],
            "pick": 1,
            "attempts": 1,
            "picked": [],
            "result": None,
            "detail": [{"team": names.get(r["team"], "?"), "games": r["n"]} for r in rows[:CAREER_OPTIONS]
                       if r["team"] in names],
        }
    return None


def build_questions(rng=None) -> list[dict]:
    rng = rng or random
    questions = [q for q in (winner_question(rng), roster_question(rng), career_question(rng)) if q]
    if len(questions) < 3:
        raise GameError("Le quiz a besoin de plus de données importées (tournois internationaux et ligues).")
    return questions


# --------------------------------------------------------------- Le déroulé
def start(user, now=None, rng=None) -> QuizAttempt:
    existing = current_attempt(user, now)
    if existing:
        return existing
    questions = build_questions(rng)
    try:
        with transaction.atomic():
            return QuizAttempt.objects.create(user=user, period_start=period_start(now), questions=questions)
    except IntegrityError:  # deux clics en même temps
        return current_attempt(user, now)


def answer(user, attempt: QuizAttempt, picked: list[str], rng=None) -> dict:
    """Répond à la question en cours. Renvoie la question mise à jour."""
    if attempt.user_id != user.pk:
        raise GameError("Ce quiz n'est pas le tien.")
    with transaction.atomic():
        attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
        index = attempt.current
        if index is None:
            raise GameError("Ce quiz est terminé.")
        question = attempt.questions[index]
        picked = [str(p) for p in picked if str(p) in {o["id"] for o in question["options"]}]
        picked = list(dict.fromkeys(picked))
        if len(picked) != question["pick"]:
            raise GameError(f"Choisis {question['pick']} réponse{'s' if question['pick'] > 1 else ''}.")
        question["picked"] = [*question["picked"], picked]
        right = sorted(picked) == sorted(question["answer"])
        question["attempts"] -= 1
        if right:
            question["result"] = True
            attempt.score += 1
        elif question["attempts"] <= 0:
            question["result"] = False
        attempt.questions = [*attempt.questions[:index], question, *attempt.questions[index + 1:]]
        if attempt.current is None:
            _finish(attempt, rng)
        attempt.save()
    return question


def _reward_pool(rarity: Rarity, count: int, rng) -> list[int]:
    ids = list(Card.objects.filter(is_active=True, split__is_playable=True).values_list("pk", "player_id"))
    rng.shuffle(ids)
    chosen, players = [], set()
    for pk, player in ids:
        if player not in players:
            players.add(player)
            chosen.append(pk)
        if len(chosen) >= count:
            break
    return chosen


def _finish(attempt: QuizAttempt, rng=None) -> None:
    rng = rng or random
    attempt.finished_at = timezone.now()
    count, rank = REWARDS[attempt.score]
    rarities = list(Rarity.objects.order_by("order"))
    rarity = rarities[min(rank, len(rarities) - 1)]
    attempt.reward_rarity = rarity
    choices = _reward_pool(rarity, count, rng)
    attempt.save()
    attempt.reward_choices.set(choices)
    if count == 1 and choices:  # 0 bonne réponse : la carte est donnée d'office
        add_copies(attempt.user, choices[0], rarity.pk)
        attempt.chosen_id = choices[0]


def choose(user, attempt: QuizAttempt, card_id: int) -> Card:
    if attempt.user_id != user.pk:
        raise GameError("Ce quiz n'est pas le tien.")
    with transaction.atomic():
        attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.finished_at is None:
            raise GameError("Termine d'abord le quiz.")
        if attempt.chosen_id:
            raise GameError("Tu as déjà pris ta carte.")
        card = attempt.reward_choices.filter(pk=card_id).first()
        if card is None:
            raise GameError("Choisis une des cartes proposées.")
        add_copies(user, card.pk, attempt.reward_rarity_id)
        attempt.chosen = card
        attempt.save(update_fields=["chosen"])
    refresh_rosters_of_user(user)
    return card
