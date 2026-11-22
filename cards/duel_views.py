"""Les pages du 1V1 (cards/duel.py). La page d'un duel reprend celle d'un Bo5
de tournoi (static/js/tournament.js, en mode « duel ») : elle demande l'état
toutes les 2 s et le redessine."""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import duel as game
from . import weekly
from .models import ROLES, Card, CardChampion, Duel, Rarity
from .services import GameError, copies_by_card
from .tournament import unplayed_rating
from .tournament_views import _card_json, _champion_tiles


@login_required
def duel_home(request):
    duels = list(game.duels_of(request.user)[:60])
    for d in duels:
        game.refresh(d)
    duels = list(game.duels_of(request.user)[:60])
    for d in duels:
        d.me = d.side_of(request.user)
        d.them = d.opponent if d.me == "a" else d.challenger
        d.my_wins = d.wins_a if d.me == "a" else d.wins_b
        d.their_wins = d.wins_b if d.me == "a" else d.wins_a
    return render(request, "cards/duels.html", {
        "received": [d for d in duels if d.status == Duel.Status.INVITED and d.me == "b"],
        "sent": [d for d in duels if d.status == Duel.Status.INVITED and d.me == "a"],
        "ongoing": [d for d in duels if d.status in (Duel.Status.LINEUP, Duel.Status.DRAFT)],
        "history": [d for d in duels if d.status == Duel.Status.FINISHED][:20],
        "players": game.opponents_for(request.user).values_list("username", flat=True),
        "missing": game.missing_roles(request.user),
        "turn_seconds": game.TURN_SECONDS,
    })


@login_required
@require_POST
def duel_invite(request):
    name = (request.POST.get("username") or "").strip()
    target = get_user_model().objects.filter(username__iexact=name).first()
    try:
        duel = game.invite(request.user, target)
    except GameError as exc:
        messages.error(request, str(exc) if target or not name else f"Aucun joueur « {name} ».")
        return redirect("cards:duels")
    messages.success(request, f"Invitation envoyée à {duel.opponent.username}. Le duel commence dès qu'il accepte.")
    return redirect("cards:duels")


@login_required
@require_POST
def duel_respond(request, pk):
    duel = get_object_or_404(Duel, pk=pk, opponent=request.user)
    accept = request.POST.get("answer") == "accept"
    try:
        duel = game.respond(request.user, duel, accept)
    except GameError as exc:
        messages.error(request, str(exc))
        return redirect("cards:duels")
    if accept:
        return redirect("cards:duel", pk=duel.pk)
    messages.success(request, "Invitation refusée.")
    return redirect("cards:duels")


@login_required
@require_POST
def duel_cancel(request, pk):
    duel = get_object_or_404(Duel, pk=pk, challenger=request.user)
    try:
        game.cancel(request.user, duel)
        messages.success(request, "Invitation annulée.")
    except GameError as exc:
        messages.error(request, str(exc))
    return redirect("cards:duels")


def _duel(request, pk) -> Duel:
    duel = get_object_or_404(Duel.objects.select_related("challenger", "opponent", "winner"), pk=pk)
    if duel.side_of(request.user) is None:
        from django.http import Http404

        raise Http404
    return game.refresh(duel)


# --------------------------------------------------------------------- État vu par un joueur
def _team(team: str, me: str) -> str:
    return "user" if team == me else "opponent"


def _flip_special(special: dict | None, me: str) -> dict | None:
    if not special:
        return None
    return {"user": special.get(me), "opponent": special.get(game.other(me))}


def state_payload(duel: Duel, user) -> dict:
    me = duel.side_of(user)
    them = game.other(me)
    them_user = duel.user_of(them)
    my_lineup, their_lineup = getattr(duel, f"lineup_{me}"), getattr(duel, f"lineup_{them}")
    both = bool(my_lineup and their_lineup)
    cards = Card.objects.select_related("player", "split", "image").in_bulk(
        [int(c) for c in list(my_lineup.values()) + (list(their_lineup.values()) if both else [])])
    rarities = Rarity.objects.in_bulk([int(r) for r in list(getattr(duel, f"rarities_{me}").values())
                                       + list(getattr(duel, f"rarities_{them}").values())])

    def lineup(side_lineup, side_rarities):
        return [_card_json(cards.get(int(side_lineup[r])), rarities.get(int(side_rarities[r]))) for r in ROLES]

    status = duel.status
    if status == Duel.Status.FINISHED:
        status = "won" if duel.winner_id == user.pk else "lost"
    payload = {
        "id": duel.pk,
        "mode": "duel",
        "status": status,
        "raw_status": duel.status,
        "wins": getattr(duel, f"wins_{me}"),
        "losses": getattr(duel, f"wins_{them}"),
        "team": them_user.username,
        "lineup": lineup(my_lineup, getattr(duel, f"rarities_{me}")) if my_lineup else [],
        "opponent": lineup(their_lineup, getattr(duel, f"rarities_{them}")) if both else [None] * len(ROLES),
        "lineup_done": bool(my_lineup),
        "opponent_ready": bool(their_lineup),
        "fearless": sorted(duel.used_champions()),
        "deadline": duel.deadline.isoformat() if duel.deadline else None,
        "auto_forced": getattr(duel, f"auto_{me}"),
        "missed": getattr(duel, f"missed_{me}"),
        "opponent_auto": getattr(duel, f"auto_{them}"),
        "forfeit": duel.forfeit,
        "meta": [{"role": r, **duel.meta[r]} for r in ROLES if r in (duel.meta or {})],
        "games": [
            {
                "number": g["number"], "won": g["winner"] == me, "side": "blue" if g["blue"] == me else "red",
                "user_score": g[f"score_{me}"], "opponent_score": g[f"score_{them}"],
                "user_coherence": g[f"coherence_{me}"], "opponent_coherence": g[f"coherence_{them}"],
                "bans": {"user": g["bans"][me], "opponent": g["bans"][them]},
                "special": _flip_special(g.get("special"), me),
                "actions": [dict(a, team=_team(a["team"], me)) for a in g.get("actions", [])],
                "rows": [{"role": r, "user": g[me][r], "opponent": g[them][r]} for r in ROLES],
            }
            for g in reversed(duel.games)
        ],
        "reward": None,
        "next_at": None,
        "draft": None,
    }
    if duel.status == Duel.Status.DRAFT and duel.draft:
        state = game.draft_state(duel)
        turn = state["turn"]
        payload["draft"] = {
            "side": "blue" if duel.draft["blue"] == me else "red",
            "bans": {"user": state["bans"][me], "opponent": state["bans"][them]},
            "picks": {"user": state["picks"][me], "opponent": state["picks"][them]},
            "turn": dict(turn, team=_team(turn["team"], me)) if turn else None,
            "special": _flip_special(duel.draft.get("special"), me),
            "actions": [dict(a, team=_team(a["team"], me)) for a in duel.draft.get("actions", [])],
            "taken": sorted(state["taken"]),
            "step": len(duel.draft.get("actions", [])) + 1, "steps": len(game.DRAFT_ORDER),
        }
    return payload


def static_payload(duel: Duel, user) -> dict:
    me = duel.side_of(user)
    them = game.other(me)
    rules = duel.meta or {}
    payload = {"champions": _champion_tiles(), "roles": ROLES,
               "metaChampions": {r: {str(k): v for k, v in m.items()} for r, m in weekly.champion_bonuses(rules).items()}}
    my_lineup = getattr(duel, f"lineup_{me}")
    if not my_lineup:
        copies = copies_by_card(user)
        mine = Card.objects.filter(pk__in=list(copies)).select_related("player", "split__league", "image", "team")
        choices = {r: [] for r in ROLES}
        for card in sorted(mine, key=lambda c: -c.overall):
            if card.role in choices:
                entry = _card_json(card, copies[card.pk][-1][0])
                entry["meta"] = weekly.card_bonus(rules.get(card.role), card)
                choices[card.role].append(entry)
        for role in ROLES:
            choices[role].sort(key=lambda c: (-c["meta"], -c["overall"]))
        payload["choices"] = choices
        return payload
    cards = Card.objects.select_related("stats", "player").in_bulk([int(c) for c in my_lineup.values()])
    ratings = {}
    for role in ROLES:
        card = cards[int(my_lineup[role])]
        player_rating = card.stats.score if card.stats else 50.0
        ratings[role] = {"base": round(unplayed_rating(player_rating)), "played": {}}
    by_card = {int(my_lineup[r]): r for r in ROLES}
    for a in CardChampion.objects.filter(card_id__in=list(by_card)):
        ratings[by_card[a.card_id]]["played"][a.champion_id] = [round(a.rating), a.games + a.history_games]
    payload["ratings"] = ratings
    their_lineup = getattr(duel, f"lineup_{them}")
    if their_lineup:
        theirs = Card.objects.select_related("player").in_bulk([int(c) for c in their_lineup.values()])
        hints = {}
        for a in CardChampion.objects.filter(card_id__in=list(theirs), games__gt=0).order_by("-games"):
            hints.setdefault(str(a.champion_id), []).append([theirs[a.card_id].player.ign, a.games])
        payload["hints"] = hints
    return payload


@login_required
def duel_page(request, pk):
    duel = _duel(request, pk)
    if duel.status in (Duel.Status.INVITED, Duel.Status.DECLINED, Duel.Status.CANCELLED, Duel.Status.EXPIRED):
        messages.info(request, {"invited": "Ce duel attend encore la réponse à l'invitation."}.get(
            duel.status, "Ce duel n'a pas eu lieu."))
        return redirect("cards:duels")
    return render(request, "cards/duel.html", {
        "duel": duel,
        "state": state_payload(duel, request.user),
        "static": static_payload(duel, request.user),
        "action_url": reverse("cards:duel_action", args=[duel.pk]),
        "state_url": reverse("cards:duel_state", args=[duel.pk]),
    })


@login_required
def duel_state(request, pk):
    duel = _duel(request, pk)
    payload = {"state": state_payload(duel, request.user)}
    if request.GET.get("static"):
        payload["static"] = static_payload(duel, request.user)
    return JsonResponse(payload)


@login_required
@require_POST
def duel_action(request, pk):
    """{"type": "lineup", "picks"} | {"type": "ban"|"pick", "champion", "role", "auto"} | {"type": "forfeit"}."""
    duel = _duel(request, pk)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Requête illisible."}, status=400)
    kind = data.get("type")
    before = len(duel.games)
    try:
        if kind == "lineup":
            duel = game.set_lineup(request.user, duel, data.get("picks") or {})
        elif kind in ("ban", "pick"):
            champion = "auto" if data.get("auto") else data.get("champion")
            duel = game.play(request.user, duel, kind, champion, data.get("role"))
        elif kind == "forfeit":
            duel = game.forfeit(request.user, duel)
        else:
            return JsonResponse({"error": "Coup inconnu."}, status=400)
    except GameError as exc:
        duel = _duel(request, pk)
        return JsonResponse({"error": str(exc), "state": state_payload(duel, request.user)}, status=409)
    duel = _duel(request, pk)
    played = duel.games[before]["number"] if len(duel.games) > before else None
    response = {"state": state_payload(duel, request.user), "played": played}
    if kind == "lineup":
        response["static"] = static_payload(duel, request.user)
    return JsonResponse(response)
