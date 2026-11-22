"""Les pages du mode tournoi (voir cards/tournament.py)."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from esport.models import Champion

from . import tournament as game
from .models import ROLES, Card, CardChampion, Rarity, Tournament, TournamentSeries
from .services import GameError, copies_by_card


def tournament_home(request):
    game.ensure_tournament()
    tournaments = game.open_tournaments()
    rarities = list(Rarity.objects.order_by("order"))
    for t in tournaments:
        total = len(t.opponents)
        t.status = None
        beaten = 0
        if request.user.is_authenticated:
            run = game.get_run(request.user, t)
            t.status = game.run_status(run)
            beaten = run.step
        t.ladder = [
            {
                "rank": total - i,
                "name": o["name"],
                "rarity": rarities[round(i / (total - 1) * (len(rarities) - 1))] if total > 1 else rarities[-1],
                "reward": game.reward_rarity(i, total),
                "beaten": i < beaten,
                "current": i == beaten,
            }
            for i, o in enumerate(t.opponents)
        ]
        t.beaten = beaten
    return render(request, "cards/tournament.html", {"tournaments": tournaments})


@login_required
@require_POST
def tournament_start(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    try:
        series = game.start_series(request.user, tournament)
    except GameError as exc:
        messages.error(request, str(exc))
        return redirect("cards:tournament")
    return redirect("cards:tournament_series", pk=series.pk)


def _series(request, pk) -> TournamentSeries:
    return get_object_or_404(
        TournamentSeries.objects.select_related("run__tournament__split", "team", "opponent_rarity",
                                                "reward_card__player", "reward_card__image", "reward_rarity"),
        pk=pk, run__user=request.user,
    )


# Le Bo5 tient sur une seule page : elle se charge une fois, puis chaque
# coup (choix des joueurs, ban, pick) est une petite requête JSON qui
# renvoie le nouvel état ; static/js/tournament.js redessine la page.

def _rarity_json(rarity):
    return {"name": rarity.name, "color": rarity.color, "bonus": float(rarity.score_bonus)} if rarity else None


def _card_json(card, rarity=None):
    if card is None:
        return None
    return {
        "id": card.pk, "ign": card.player.ign, "role": card.role, "overall": card.overall,
        "photo": card.image_url, "split": card.split.name, "rarity": _rarity_json(rarity),
    }


def _cards(ids):
    return Card.objects.select_related("player", "split", "image").in_bulk([int(c) for c in ids])


def state_payload(series: TournamentSeries) -> dict:
    """Tout ce qui change pendant le Bo5."""
    tournament = series.run.tournament
    cards = _cards(list(series.opponent.values()) + list(series.lineup.values()))
    rarities = Rarity.objects.in_bulk([int(r) for r in series.lineup_rarities.values()])
    payload = {
        "id": series.pk,
        "status": series.status,
        "wins": series.wins,
        "losses": series.losses,
        "step": series.step + 1,
        "total": len(tournament.opponents),
        "team": series.team.name if series.team else "?",
        "tournament": tournament.title,
        "opponent_rarity": _rarity_json(series.opponent_rarity),
        "opponent": [_card_json(cards.get(int(series.opponent[r])), series.opponent_rarity) for r in ROLES],
        "lineup": [
            _card_json(cards.get(int(series.lineup[r])), rarities.get(int(series.lineup_rarities[r])))
            for r in ROLES
        ] if series.lineup else [],
        "fearless": sorted(series.used_champions()),
        "games": [
            {
                "number": g["number"], "won": g["won"], "side": g.get("side"),
                "user_score": g["user_score"], "opponent_score": g["opponent_score"],
                "user_coherence": g["user_coherence"], "opponent_coherence": g["opponent_coherence"],
                "bans": g.get("bans"),
                "rows": [{"role": r, "user": g["user"][r], "opponent": g["opponent"][r]} for r in ROLES],
            }
            for g in reversed(series.games)
        ],
        "reward": _card_json(series.reward_card, series.reward_rarity),
        "next_at": None,
        "draft": None,
    }
    if series.is_over:
        status = game.run_status(series.run)
        payload["next_at"] = status["next_at"].isoformat() if status["next_at"] else None
    if series.status == TournamentSeries.Status.DRAFT:
        state = game.draft_state(series)
        payload["draft"] = {
            "side": state["side"], "bans": state["bans"], "picks": state["picks"], "turn": state["turn"],
            "taken": sorted(state["taken"]),
            "step": len((series.draft or {}).get("actions", [])) + 1, "steps": len(game.DRAFT_ORDER),
        }
    return payload


def _champion_tiles() -> list[dict]:
    from django.core.cache import cache

    tiles = cache.get("champions:tiles")
    if tiles is None:
        tiles = [{"id": c.pk, "name": c.name, "icon": c.icon_url} for c in Champion.objects.order_by("name")]
        cache.set("champions:tiles", tiles, 3600)
    return tiles


def static_payload(series: TournamentSeries, user) -> dict:
    """Ce qui ne change pas d'une partie à l'autre : les champions, les notes
    des joueurs alignés, ce que l'adversaire a joué ; et, avant le choix des
    joueurs, la collection."""
    payload = {"champions": _champion_tiles(), "roles": ROLES}
    if series.status == TournamentSeries.Status.LINEUP:
        copies = copies_by_card(user)
        mine = Card.objects.filter(pk__in=list(copies)).select_related("player", "split", "image")
        choices = {r: [] for r in ROLES}
        for card in sorted(mine, key=lambda c: -c.overall):
            if card.role in choices:
                choices[card.role].append(_card_json(card, copies[card.pk][-1][0]))
        payload["choices"] = choices
    elif series.lineup:
        cards = Card.objects.select_related("stats").in_bulk([int(c) for c in series.lineup.values()])
        ratings = {}
        for role in ROLES:
            card = cards[int(series.lineup[role])]
            player_rating = card.stats.score if card.stats else 50.0
            ratings[role] = {"base": round(game.unplayed_rating(player_rating)), "played": {}}
        by_card = {int(series.lineup[r]): r for r in ROLES}
        for a in CardChampion.objects.filter(card_id__in=list(by_card)):
            ratings[by_card[a.card_id]]["played"][a.champion_id] = [round(a.rating), a.games + a.history_games]
        payload["ratings"] = ratings
        prepared = game.prepare(series)
        opponents = _cards(series.opponent.values())
        hints = {}
        for role in ROLES:
            ign = opponents[int(series.opponent[role])].player.ign
            for cid, n in prepared.get("pools", {}).get(role, []):
                hints.setdefault(str(cid), []).append([ign, n])
        payload["hints"] = hints
    return payload


@login_required
def tournament_series(request, pk):
    series = _series(request, pk)
    return render(request, "cards/tournament_series.html", {
        "series": series,
        "tournament": series.run.tournament,
        "state": state_payload(series),
        "static": static_payload(series, request.user),
        "action_url": reverse("cards:tournament_action", args=[series.pk]),
    })


@login_required
@require_POST
def tournament_action(request, pk):
    """Un coup : {"type": "lineup", "picks": {poste: carte}} | {"type": "ban",
    "champion": id} | {"type": "pick", "champion": id, "role": poste}.
    Renvoie le nouvel état (et ce qui ne change plus, après le choix des joueurs)."""
    series = _series(request, pk)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Requête illisible."}, status=400)
    kind = data.get("type")
    played = None
    try:
        if kind == "lineup":
            game.set_lineup(request.user, series, data.get("picks") or {})
        elif kind in {"ban", "pick"}:
            state = game.draft_state(series)
            if state["turn"] and state["turn"]["type"] != kind:
                raise GameError("Ce n'est pas le moment.")
            played = game.draft_action(request.user, series, data.get("champion"), role=data.get("role"))
        else:
            return JsonResponse({"error": "Coup inconnu."}, status=400)
    except GameError as exc:
        series = _series(request, pk)
        return JsonResponse({"error": str(exc), "state": state_payload(series)}, status=409)
    series = _series(request, pk)
    response = {"state": state_payload(series), "played": played["number"] if played else None}
    if kind == "lineup":
        response["static"] = static_payload(series, request.user)
    return JsonResponse(response)
