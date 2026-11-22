"""Les pages du mode tournoi (voir cards/tournament.py)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
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
                                                "reward_card__player", "reward_rarity"),
        pk=pk, run__user=request.user,
    )


@login_required
def tournament_series(request, pk):
    series = _series(request, pk)
    context = {"series": series, "roles": ROLES, **game.series_view(series)}
    context["tournament"] = series.run.tournament
    context["total"] = len(series.run.tournament.opponents)
    if series.status == TournamentSeries.Status.LINEUP:
        context["choices"] = _lineup_choices(request.user)
    elif series.status == TournamentSeries.Status.DRAFT:
        context["board"] = _draft_board(series)
    context["lineup_rarities"] = Rarity.objects.in_bulk([int(r) for r in series.lineup_rarities.values()])
    context["games"] = list(reversed(series.games))
    context["run_status"] = game.run_status(series.run)
    return render(request, "cards/tournament_series.html", context)


def _lineup_choices(user) -> list[tuple[str, list]]:
    copies = copies_by_card(user)
    cards = Card.objects.filter(pk__in=list(copies)).select_related("player", "split", "stats")
    by_role: dict[str, list] = {r: [] for r in ROLES}
    for card in sorted(cards, key=lambda c: -c.overall):
        if card.role in by_role:
            card.best = copies[card.pk][-1][0]
            by_role[card.role].append(card)
    return [(r, by_role[r]) for r in ROLES]


def _draft_board(series: TournamentSeries) -> dict:
    """La draft en cours : bans et picks des deux côtés, et le tour du
    joueur du site avec ses choix possibles."""
    from esport.models import GameAppearance

    state = game.draft_state(series)
    view = game.series_view(series)
    mine, theirs = dict(view["lineup"]), dict(view["opponent"])
    all_champions = list(Champion.objects.order_by("name"))
    by_id = {c.pk: c for c in all_champions}
    free = [c for c in all_champions if c.pk not in state["taken"]]

    def side(team, cards):
        return {
            "bans": [by_id.get(c) for c in state["bans"][team]],
            "slots": [{"role": r, "card": cards[r], "champion": by_id.get(state["picks"][team].get(r))} for r in ROLES],
        }

    board = {
        "user_color": state["side"],
        "user": side("user", mine),
        "opponent": side("opponent", theirs),
        "turn": state["turn"],
        "step": len((series.draft or {}).get("actions", [])) + 1,
        "steps": len(game.DRAFT_ORDER),
    }
    turn = state["turn"]
    if turn and turn["team"] == "user" and turn["type"] == "ban":
        # Pour bannir : ce que les joueurs d'en face ont joué pendant le tournoi.
        pools: dict[str, list[str]] = {}
        rows = (
            GameAppearance.objects.filter(tournament__split=series.run.tournament.split_id,
                                          player__in=[c.player_id for c in theirs.values()])
            .values("champion", "player__ign").annotate(n=Count("id")).order_by("-n")
        )
        for r in rows:
            pools.setdefault(r["champion"], []).append(f"{r['player__ign']} ×{r['n']}")
        board["ban_options"] = sorted(
            ({"id": c.pk, "name": c.name, "played": ", ".join(pools.get(c.name, []))} for c in free),
            key=lambda o: (not o["played"], o["name"]),
        )
    elif turn and turn["team"] == "user":
        groups = []
        for role in ROLES:
            if role in state["picks"]["user"]:
                continue
            card = mine[role]
            player_rating = card.stats.score if card.stats else 50.0
            played = {a.champion_id: a for a in CardChampion.objects.filter(card=card)}
            options = []
            for c in free:
                a = played.get(c.pk)
                options.append({
                    "value": f"{role}|{c.pk}", "name": c.name, "played": a is not None,
                    "rating": round(a.rating if a else game.unplayed_rating(player_rating)),
                    "games": (a.games + a.history_games) if a else 0,
                })
            options.sort(key=lambda o: (not o["played"], -o["rating"], o["name"]))
            groups.append({"role": role, "card": card, "options": options})
        board["pick_groups"] = groups
    return board


@login_required
@require_POST
def tournament_lineup(request, pk):
    series = _series(request, pk)
    try:
        game.set_lineup(request.user, series, {r: request.POST.get(r) for r in ROLES})
    except GameError as exc:
        messages.error(request, str(exc))
    return redirect("cards:tournament_series", pk=series.pk)


@login_required
@require_POST
def tournament_draft(request, pk):
    series = _series(request, pk)
    choice = request.POST.get("choice", "")
    role, _, champion = choice.partition("|") if "|" in choice else (None, "", choice)
    try:
        played = game.draft_action(request.user, series, champion, role=role)
    except GameError as exc:
        messages.error(request, str(exc))
        return redirect("cards:tournament_series", pk=series.pk)
    url = reverse("cards:tournament_series", args=[series.pk])
    return redirect(url + (f"#partie-{played['number']}" if played else "#draft"))
