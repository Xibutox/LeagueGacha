"""Les pages de la Fantasy League (cards/fantasy.py) : un sous-onglet par
compétition, et pour chacune l'équipe, la semaine, le classement et les
règles."""
from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import fantasy, fantasy_sim
from .models import ROLES, Card, FantasyCompetition, FantasyLineup, FantasyWeek
from .services import GameError

VIEWS = {"equipe": "Mon équipe", "semaine": "Semaine", "classement": "Classement", "regles": "Règles"}
ROLE_LABELS = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "Bot": "Bot", "Support": "Support", "sub": "Remplaçant"}
#: Les points en direct sont recalculés au plus toutes les 10 min par compétition.
LIVE_SECONDS = 600


def _refresh(comp: FantasyCompetition) -> None:
    key = f"fantasy:advance:{comp.pk}"
    if cache.add(key, 1, LIVE_SECONDS):
        fantasy.advance(comp)


def fantasy_home(request):
    comps = fantasy.active_competitions()
    if not comps and cache.add("fantasy:sync", 1, LIVE_SECONDS):
        fantasy.sync_competitions()
        comps = fantasy.active_competitions()
    if not comps:
        return render(request, "cards/fantasy.html", {"comps": []})
    return redirect("cards:fantasy_comp", slug=comps[0].slug)


def _photos(player_ids) -> dict[int, str]:
    """La photo la plus récente de chaque joueur (celle de sa dernière carte)."""
    out = {}
    for card in (Card.objects.filter(player_id__in=player_ids).select_related("image", "player")
                 .order_by("player_id", "-split__year", "-pk")):
        if card.player_id not in out:
            out[card.player_id] = card.image_url
    return out


def _slots(lineup: FantasyLineup | None, pws: dict) -> list[dict]:
    """Les 6 places d'une équipe pour l'affichage."""
    detail = {s["role"]: s for s in (lineup.detail or {}).get("slots", [])} if lineup else {}
    out = []
    for slot in ROLES + ["sub"]:
        pid = (lineup.players or {}).get(slot) if lineup else None
        pw = pws.get(pid)
        d = detail.get(slot, {})
        shown = pws.get(d.get("player")) if d.get("replaced") else pw
        out.append({
            "slot": slot, "label": ROLE_LABELS[slot], "pw": pw, "shown": shown, "replaced": d.get("replaced"),
            "points": d.get("points", pw.points if pw else 0) if slot != "sub" else (pw.points if pw else 0),
            "captain": lineup is not None and lineup.captain == slot,
        })
    return out


def fantasy_page(request, slug):
    comp = get_object_or_404(FantasyCompetition, slug=slug)
    _refresh(comp)
    view = request.GET.get("vue") if request.GET.get("vue") in VIEWS else "equipe"
    now = timezone.now()
    current = fantasy.current_week(comp, now)
    upcoming = fantasy.editable_week(comp, now)
    if upcoming is not None and current is not None and upcoming.pk == current.pk:
        upcoming = None
    shown = current or FantasyWeek.objects.filter(competition=comp).exclude(status=FantasyWeek.Status.OPEN) \
        .order_by("-start").first()
    ctx = {
        "comp": comp, "comps": [(c, fantasy.tab_label(c)) for c in
                                                     fantasy.active_competitions(now)],
        "views": VIEWS, "view": view, "current": current, "upcoming": upcoming, "shown": shown,
        "label": fantasy.tab_label(comp), "fictional": comp.kind == FantasyCompetition.Kind.FICTIONAL,
    }
    user = request.user if request.user.is_authenticated else None
    if view == "equipe":
        ctx.update(_team_context(user, comp, current, upcoming))
    elif view == "semaine" and shown is not None:
        ctx.update(_week_context(user, comp, shown, now))
    elif view == "classement":
        ctx.update(_ranking_context(user, comp, shown))
    elif view == "regles":
        ctx.update(_rules_context())
    return render(request, "cards/fantasy.html", ctx)


def _team_context(user, comp, current, upcoming) -> dict:
    ctx = {}
    if current is not None and user is not None:
        pws = fantasy.players_of_week(current)
        lineup = FantasyLineup.objects.filter(user=user, week=current).first()
        ctx["current_lineup"] = lineup
        ctx["current_slots"] = _slots(lineup, pws) if lineup else None
    if upcoming is None:
        return ctx
    pws = fantasy.players_of_week(upcoming)
    owned = fantasy.owned_players(user) if user else set()
    mine = None
    if user is not None:
        mine = FantasyLineup.objects.filter(user=user, week=upcoming).first()
        if mine is None and current is not None:
            mine = FantasyLineup.objects.filter(user=user, week=current).first()
    photos = _photos([pid for pid in pws if pid in owned])
    choices = defaultdict(list)
    for pw in sorted(pws.values(), key=lambda p: (-p.cost, p.player.ign.lower())):
        if pw.player_id in owned:
            choices[pw.role].append(pw)
            pw.photo = photos.get(pw.player_id, "")
    ctx.update({
        "upcoming_lineup": mine, "choices": [(r, choices.get(r, [])) for r in ROLES],
        "all_choices": [pw for r in ROLES for pw in choices.get(r, [])],
        "picked": (mine.players if mine else {}), "captain": mine.captain if mine else "",
        "missing": [r for r in ROLES if not choices.get(r)], "lock_at": fantasy.lock_time(upcoming),
        "players_json": {pw.player_id: {"cost": pw.cost, "team": pw.team, "role": pw.role, "ign": pw.player.ign,
                                        "photo": pw.photo} for r in ROLES for pw in choices.get(r, [])},
        "pool_size": len(pws),
    })
    return ctx


def _week_context(user, comp, week, now) -> dict:
    pws = fantasy.players_of_week(week)
    top = sorted((pw for pw in pws.values() if pw.games), key=lambda p: -p.points)[:40]
    photos = _photos([pw.player_id for pw in top])
    for pw in top:
        pw.photo = photos.get(pw.player_id, "")
    lineup = FantasyLineup.objects.filter(user=user, week=week).first() if user else None
    ctx = {"week": week, "top": top, "lineup": lineup, "slots": _slots(lineup, pws) if lineup else None,
           "ideal": [(r, pws.get(pid)) for r, pid in (week.ideal or {}).items()]}
    if comp.kind == FantasyCompetition.Kind.FICTIONAL:
        last = fantasy.revealed_day(week, now)
        days = defaultdict(list)
        for m in week.matches.all():
            days[m.day].append(m)
        ctx["days"] = [{"day": d, "matches": ms, "revealed": d <= last} for d, ms in sorted(days.items())]
        ctx["standings"] = fantasy_sim.standings(comp, now)[:16]
    return ctx


def _ranking_context(user, comp, week) -> dict:
    weekly = []
    if week is not None:
        weekly = list(week.lineups.select_related("user").order_by("-points", "user__username")[:100])
        for rank, lineup in enumerate(weekly, 1):
            lineup.rank = rank
    season = fantasy.season_ranking(comp)[:100]
    return {"weekly": weekly, "season": season, "tier_labels": fantasy.TIER_LABELS,
            "ideal_score": week.ideal_score if week else 0}


def _rules_context() -> dict:
    return {
        "scale": [
            ("Kill", f"+{fantasy.KILL}"), ("Assist", f"+{fantasy.ASSIST}"), ("Mort", str(fantasy.DEATH)),
            ("CS", f"+1 pour {fantasy.CS_PER_POINT} CS"), ("Score de vision", f"+1 pour {fantasy.VISION_PER_POINT}"),
            (f"{fantasy.BIG_GAME} kills ou {fantasy.BIG_GAME} assists dans la partie", f"+{fantasy.BIG_GAME_POINTS}"),
            ("Victoire", f"+{fantasy.WIN}"), (f"Victoire en moins de {fantasy.FAST_WIN_MINUTES} min",
                                              f"+{fantasy.FAST_WIN}"),
        ],
        "tiers": [(fantasy.TIER_LABELS[name], round(share * 100), n) for share, name, n in fantasy.TIERS],
        "rules": fantasy,
    }


@login_required
@require_POST
def fantasy_save(request, slug):
    comp = get_object_or_404(FantasyCompetition, slug=slug)
    week = fantasy.editable_week(comp)
    if week is None:
        messages.error(request, "Il n'y a plus de semaine à composer dans cette compétition.")
    else:
        picks = {slot: request.POST.get(slot) for slot in ROLES + ["sub"]}
        try:
            fantasy.save_lineup(request.user, week, picks, request.POST.get("captain", ""))
            messages.success(request, f"Équipe enregistrée pour la semaine {week.number}.")
        except (GameError, ValueError) as exc:
            messages.error(request, str(exc) if isinstance(exc, GameError) else "Choix invalide.")
    return redirect(reverse("cards:fantasy_comp", kwargs={"slug": slug}) + "?vue=equipe")

