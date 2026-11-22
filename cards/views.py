import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from esport.models import Champion, Split
from esport.scoring import COHERENCE_WEIGHT, DIMENSIONS, SLOT_PLAYER_WEIGHT, UNPLAYED_PENALTY

from esport.models import Team

from .models import COMPOSITIONS, ROLES, Booster, Card, CardChampion, PackOpening, Rarity, Roster, UserCard
from . import catalog, weekly
from .services import (
    booster_showcase,
    choose_exchange,
    pending_exchanges,
    ensure_daily_portal,
    open_portal,
    open_portals,
    portal_status,
    GameError,
    best_rarities,
    booster_status,
    copies_by_card,
    evaluate_roster,
    exchange,
    fuse,
    leaderboard,
    open_booster,
    roster_picks,
    save_roster,
    set_supporter_team,
    supporter_team,
    unseen_gifts,
)


def signup(request):
    if request.user.is_authenticated:
        return redirect("cards:home")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Bienvenue ! Tes premiers boosters t'attendent.")
        return redirect("cards:home")
    return render(request, "registration/signup.html", {"form": form})


def _playable_cards():
    return Card.objects.filter(is_active=True, split__is_playable=True)


def _with_status(user, boosters):
    """Ajoute à chaque booster sa réserve (disponibles, max, prochain)."""
    boosters = list(boosters)
    if user.is_authenticated:
        status = booster_status(user, boosters)
        for booster in boosters:
            booster.status = status[booster.pk]
    return boosters


def home(request):
    boosters = [
        b for b in Booster.objects.filter(is_active=True).select_related("guaranteed_rarity").prefetch_related("leagues")
        if catalog.has_cards(b)
    ]
    boosters = _with_status(request.user, boosters)
    for booster in boosters:
        booster.showcase = [] if booster.image else booster_showcase(booster)
    ensure_daily_portal()
    portals = open_portals()
    for portal in portals:
        portal.showcase = [] if portal.booster.image else booster_showcase(portal.booster)
        if request.user.is_authenticated:
            portal.status = portal_status(request.user, portal)
    rarities = list(Rarity.objects.all())
    context = {
        "boosters": boosters,
        "portals": portals,
        "rarities": rarities,
        "example_rating": EXAMPLE_RATING,
        "rarity_rows": [
            {"rarity": r, "example": round(EXAMPLE_RATING * (1 + float(r.score_bonus or 0) / 100))}
            for r in rarities
        ],
    }
    if request.user.is_authenticated:
        context["exchanges"] = list(pending_exchanges(request.user))
    if request.user.is_authenticated:
        context["unlimited"] = any(b.status["unlimited"] for b in boosters)
        context["ready"] = sum(b.status["available"] for b in boosters)
        context["owned_count"] = UserCard.objects.filter(user=request.user).values("card").distinct().count()
        context["total_count"] = _playable_cards().count()
        context["recent_openings"] = PackOpening.objects.filter(user=request.user).select_related("booster")[:5]
        context["gifts"] = unseen_gifts(request.user)
        best = Roster.objects.filter(user=request.user, week__isnull=True, score__isnull=False).order_by("-score").first()
        context["best_roster"] = best
    return render(request, "cards/home.html", context)


@login_required
@require_POST
def open_booster_view(request, slug):
    booster = get_object_or_404(Booster, slug=slug, is_active=True)
    try:
        opening = open_booster(request.user, booster)
    except GameError as exc:
        messages.error(request, str(exc))
        return redirect("cards:home")
    return redirect("cards:opening", pk=opening.pk)


@login_required
@require_POST
def open_portal_view(request, pk):
    from .models import Portal

    portal = get_object_or_404(Portal.objects.select_related("booster"), pk=pk)
    try:
        opening = open_portal(request.user, portal)
    except GameError as exc:
        messages.error(request, str(exc))
        return redirect("cards:home")
    return redirect("cards:opening", pk=opening.pk)


@login_required
def opening_detail(request, pk):
    opening = get_object_or_404(PackOpening.objects.select_related("booster", "portal"), pk=pk, user=request.user)
    items = list(
        opening.cards.select_related(
            "card__player", "card__split", "card__team", "card__image", "card__stats", "rarity"
        )
    )
    copies = copies_by_card(request.user, [i.card_id for i in items])
    for item in items:
        item.copies = copies.get(item.card_id, [])
        item.same = next((q for r, q in item.copies if r.pk == item.rarity_id), 0)
        item.can_fuse = bool(item.rarity.fusion_cost) and item.same >= item.rarity.fusion_cost
    new_count = sum(1 for item in items if item.was_new)
    fresh = timezone.now() - opening.opened_at < timedelta(minutes=10)
    latest = PackOpening.objects.filter(user=request.user).values_list("pk", flat=True).first()
    animate = fresh and latest == opening.pk and request.GET.get("anim") != "0"
    # De quoi en ouvrir un autre : sur le portail s'il vient d'un portail
    # encore ouvert, sinon dans la réserve du booster (s'il est en boutique).
    status = portal_open = None
    if opening.portal and opening.portal.is_active and opening.portal.ends_at > timezone.now():
        portal_open = opening.portal
        status = portal_status(request.user, portal_open)
    elif opening.booster and opening.booster.is_active and not opening.portal:
        status = booster_status(request.user, [opening.booster])[opening.booster.pk]
    best = max(items, key=lambda i: (i.rarity.order, i.card.overall)) if items else None
    return render(
        request,
        "cards/opening.html",
        {
            "opening": opening,
            "items": items,
            "animate": animate,
            "new_count": new_count,
            "duplicate_count": len(items) - new_count,
            "fusable": [i for i in items if i.can_fuse],
            "best": best,
            "status": status,
            "portal": portal_open,
        },
    )


#: La note d'exemple pour expliquer le bonus des raretés (accueil).
EXAMPLE_RATING = 80
COLLECTION_PAGE_SIZE = 48
PERIODS_SHOWN = 8


@login_required
def collection(request):
    cards = _playable_cards().select_related("player", "split", "team", "image", "stats")
    split_slug = request.GET.get("periode", "")
    rarity_code = request.GET.get("rarete", "")
    role = request.GET.get("role", "")
    owned_filter = request.GET.get("possession", "")
    q = request.GET.get("q", "").strip()

    if split_slug:
        cards = cards.filter(split__slug=split_slug)
    if role:
        cards = cards.filter(role=role)
    if q:
        cards = cards.filter(Q(player__ign__icontains=q) | Q(player__real_name__icontains=q) | Q(team__name__icontains=q))

    copies = copies_by_card(request.user)
    if owned_filter == "possedees":
        cards = cards.filter(pk__in=list(copies))
    elif owned_filter == "manquantes":
        cards = cards.exclude(pk__in=list(copies))
    elif owned_filter == "fusion":
        ready = [cid for cid, items in copies.items() if any(r.fusion_cost and q >= r.fusion_cost for r, q in items)]
        cards = cards.filter(pk__in=ready)
    if rarity_code:
        cards = cards.filter(pk__in=[cid for cid, items in copies.items() if any(r.code == rarity_code for r, _ in items)])

    # Par pages : avec tout l'historique importé, des milliers de cartes.
    cards = cards.order_by("-split__start_date", "-overall", "player__ign")
    page = Paginator(cards, COLLECTION_PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    rows = []
    for card in page.object_list:
        items = copies.get(card.pk, [])
        best = items[-1][0] if items else None
        rows.append({"card": card, "copies": items, "best": best,
                     "fusable": any(r.fusion_cost and q >= r.fusion_cost for r, q in items)})

    progress = (
        Split.objects.filter(is_playable=True)
        .annotate(
            total=Count("cards", filter=Q(cards__is_active=True), distinct=True),
            owned=Count("cards", filter=Q(cards__is_active=True, cards__owners__user=request.user), distinct=True),
        )
        .filter(total__gt=0)
        .select_related("league")
        .order_by("-start_date")
    )
    # Le bandeau des périodes : celles où le joueur a des cartes (ou la
    # période filtrée), sinon les plus récentes ; les autres sont dans le filtre.
    progress = list(progress)
    shown = [s for s in progress if s.owned or s.slug == split_slug] or progress[:PERIODS_SHOWN]
    return render(
        request,
        "cards/collection.html",
        {
            "rows": rows,
            "splits": [s for s in progress],
            "rarities": Rarity.objects.all(),
            "roles": ROLES,
            "progress": shown,
            "page": page,
            "query": query.urlencode(),
            "fusable_count": sum(1 for items in copies.values()
                                 if any(r.fusion_cost and q >= r.fusion_cost for r, q in items)),
            "filters": {"periode": split_slug, "rarete": rarity_code, "role": role, "possession": owned_filter,
                        "q": q},
        },
    )


@login_required
def card_detail(request, pk):
    card = get_object_or_404(Card.objects.select_related("player", "split", "team", "image", "stats"), pk=pk)
    copies = copies_by_card(request.user, [card.pk]).get(card.pk, [])
    rarities = list(Rarity.objects.select_related("exchange_rarity"))
    owned = {r.pk: q for r, q in copies}
    ladder = []
    for rarity in rarities:
        quantity = owned.get(rarity.pk, 0)
        target = next((r for r in rarities if r.order > rarity.order), None)
        ladder.append({
            "rarity": rarity,
            "quantity": quantity,
            "fusion_target": target if rarity.fusion_cost else None,
            "can_fuse": bool(rarity.fusion_cost and target and quantity >= rarity.fusion_cost),
            "can_exchange": bool(rarity.exchange_cost and rarity.exchange_rarity and quantity >= rarity.exchange_cost + 1),
        })
    best = copies[-1][0] if copies else None
    champions = card.champions.select_related("champion").order_by("-games", "-history_games", "-rating")
    dims = card.stats.dimensions if card.stats else {}
    dimensions = [(label, dims[key]) for key, (label, _) in DIMENSIONS.items() if key in dims]
    return render(
        request,
        "cards/card_detail.html",
        {"card": card, "copies": copies, "best": best, "ladder": ladder,
         "other_cards": card.player.cards.exclude(pk=card.pk).select_related("split"),
         "dimensions": dimensions, "champions": champions},
    )


@login_required
@require_POST
def fuse_view(request, pk):
    card = get_object_or_404(Card, pk=pk)
    try:
        target = fuse(request.user, card.pk, int(request.POST.get("rarity", 0)))
    except (GameError, Rarity.DoesNotExist, ValueError) as exc:
        messages.error(request, str(exc) or "Fusion impossible.")
    else:
        messages.success(request, f"Fusion réussie : {card.player.ign} passe {target.name.lower()} !")
    return redirect(request.POST.get("next") or reverse("cards:card_detail", args=[card.pk]))


@login_required
@require_POST
def exchange_view(request, pk):
    card = get_object_or_404(Card, pk=pk)
    try:
        offer = exchange(request.user, card.pk, int(request.POST.get("rarity", 0)))
    except (GameError, Rarity.DoesNotExist, ValueError) as exc:
        messages.error(request, str(exc) or "Échange impossible.")
        return redirect("cards:card_detail", pk=card.pk)
    return redirect("cards:exchange_offer", pk=offer.pk)


@login_required
def exchange_offer(request, pk):
    from .models import ExchangeOffer

    offer = get_object_or_404(
        ExchangeOffer.objects.select_related("rarity", "given_rarity", "given_card__player", "chosen"),
        pk=pk, user=request.user,
    )
    choices = list(offer.choices.select_related("player", "split", "team", "image", "stats").order_by("-overall"))
    owned = copies_by_card(request.user, [c.pk for c in choices])
    for c in choices:
        c.owned = sum(q for _, q in owned.get(c.pk, []))
    return render(request, "cards/exchange.html", {"offer": offer, "choices": choices})


@login_required
@require_POST
def choose_exchange_view(request, pk):
    try:
        card, rarity = choose_exchange(request.user, pk, int(request.POST.get("card", 0)))
    except (GameError, ValueError) as exc:
        messages.error(request, str(exc) or "Choix impossible.")
        return redirect("cards:exchange_offer", pk=pk)
    messages.success(request, f"{card.player.ign} rejoint ta collection en {rarity.name.lower()}.")
    return redirect("cards:card_detail", pk=card.pk)


# --------------------------------------------------------------------- Équipes
def _roster_items(user, rosters) -> list[dict]:
    owned = list(Card.objects.filter(owners__user=user).values_list("pk", flat=True).distinct())
    rarities = best_rarities(user, owned)
    items = []
    for roster in rosters:
        slots = roster.slot_map()
        items.append({
            "roster": roster,
            "slots": [(role, slots.get(role), rarities.get(slots[role].card_id) if role in slots else None)
                      for role in ROLES],
        })
    return items


_ROSTER_PREFETCH = (
    "slots__card__player", "slots__card__split", "slots__card__team", "slots__card__image",
    "slots__card__stats", "slots__picks__champion",
)


def _supporter_context(user) -> dict:
    team = supporter_team(user)
    # Les équipes qui ont des cartes, pour la liste de choix.
    teams = (
        Team.objects.filter(pk__in=Card.objects.filter(is_active=True).values("team"))
        .order_by("name").values_list("name", flat=True)
    )
    return {"supporter": team, "supporter_teams": list(teams)}


@login_required
def roster_list(request):
    rosters = Roster.objects.filter(user=request.user, week__isnull=True).prefetch_related(
        *_ROSTER_PREFETCH).order_by("-score", "-updated_at")
    context = {"items": _roster_items(request.user, rosters), "tab": "permanent"}
    context.update(_supporter_context(request.user))
    return render(request, "cards/rosters.html", context)


@login_required
@require_POST
def supporter_set(request):
    """Choisit l'équipe de cœur (par son nom) ; vide : la retire."""
    name = (request.POST.get("team") or "").strip()
    if not name:
        set_supporter_team(request.user, None)
        messages.success(request, "Plus d'équipe de cœur.")
        return redirect(request.POST.get("next") or "cards:rosters")
    team = Team.objects.filter(name__iexact=name).first() or Team.objects.filter(short_name__iexact=name).first()
    if team is None:
        messages.error(request, f"Aucune équipe « {name} ». Choisis-la dans la liste proposée.")
    else:
        set_supporter_team(request.user, team)
        messages.success(request, f"{team.name} est ton équipe de cœur : +1 % par joueur de {team.name} aligné.")
    next_url = request.POST.get("next") or ""
    return redirect(next_url if next_url.startswith("/") else "cards:rosters")


def _rule_list(rules: dict) -> list[dict]:
    return [{"role": role, **rules[role]} for role in ROLES if role in rules]


@login_required
def weekly_page(request):
    """L'équipe de la semaine : la méta, et l'équipe (reprise de la
    meilleure équipe permanente si le joueur n'y a pas encore touché)."""
    week = weekly.week_start()
    meta = weekly.meta_for(week)
    roster = weekly.weekly_roster(request.user, week)
    items = []
    if roster is not None:
        roster = Roster.objects.prefetch_related(*_ROSTER_PREFETCH).get(pk=roster.pk)
        items = _roster_items(request.user, [roster])
    context = {
        "tab": "weekly",
        "week": week,
        "ends_at": weekly.week_end(week),
        "rules": _rule_list(meta.rules),
        "roster": roster,
        "items": items,
        "has_permanent": Roster.objects.filter(user=request.user, week__isnull=True, score__isnull=False).exists(),
    }
    context.update(_supporter_context(request.user))
    return render(request, "cards/weekly.html", context)


@login_required
@require_POST
def weekly_reset(request):
    """Reprend la meilleure équipe permanente dans l'équipe de la semaine."""
    week = weekly.week_start()
    roster = Roster.objects.filter(user=request.user, week=week).first()
    result = weekly.copy_best_roster(request.user, week, roster)
    if result is None:
        messages.error(request, "Il te faut d'abord une équipe permanente complète.")
    else:
        messages.success(request, "Ton équipe de la semaine reprend ta meilleure équipe permanente.")
    return redirect("cards:weekly")


def _builder_data(user, roster, week=None):
    owned_ids = list(Card.objects.filter(owners__user=user, owners__quantity__gt=0).values_list("pk", flat=True).distinct())
    rarities = best_rarities(user, owned_ids)
    cards = (
        Card.objects.filter(pk__in=owned_ids)
        .select_related("player", "split__league", "team", "image", "stats")
        .prefetch_related(Prefetch("champions", queryset=CardChampion.objects.only(
            "card_id", "champion_id", "rating", "games", "wins", "history_games")))
        .order_by("-overall", "player__ign")
    )
    rules = weekly.meta_for(week).rules if week else {}
    fan = supporter_team(user)
    by_role = {role: [] for role in ROLES}
    affinities = {}
    for card in cards:
        if card.role not in by_role:
            continue
        rarity = rarities[card.pk]
        by_role[card.role].append({
            "id": card.pk,
            "ign": card.player.ign,
            "team": (card.team.short_name or card.team.name) if card.team else "",
            "split": card.split.name,
            "overall": card.overall,
            "rating": round(card.stats.score, 1) if card.stats else 50,
            "photo": card.image_url,
            "rarity": {"name": rarity.name, "color": rarity.color, "bonus": float(rarity.score_bonus)},
            "meta": weekly.card_bonus(rules.get(card.role), card),
            "fan": bool(fan and card.team_id == fan.pk),
        })
        affinities[card.pk] = {
            a.champion_id: {"rating": a.rating, "games": a.games, "wins": a.wins, "before": a.history_games}
            for a in card.champions.all()
        }
    champions = [
        {"id": c.pk, "name": c.name, "title": c.title, "icon": c.icon_url, "portrait": c.portrait_url,
         "tags": c.tags}
        for c in Champion.objects.exclude(key="").order_by("name")
    ]
    return {
        "roles": ROLES,
        "compositions": COMPOSITIONS,
        "cards": by_role,
        "affinities": affinities,
        "champions": champions,
        "penalty": UNPLAYED_PENALTY,
        "playerWeight": SLOT_PLAYER_WEIGHT,
        "coherenceWeight": COHERENCE_WEIGHT,
        "name": roster.name if roster else (weekly.WEEKLY_NAME if week else ""),
        "picks": roster_picks(roster) if roster else {},
        "previewUrl": reverse("cards:roster_preview"),
        "week": week.isoformat() if week else None,
        "rules": rules,
        "metaChampions": weekly.champion_bonuses(rules) if rules else {},
        "supporter": fan.name if fan else None,
    }


@login_required
def roster_edit(request, pk=None):
    roster = get_object_or_404(Roster, pk=pk, user=request.user) if pk else None
    current_week = weekly.week_start()
    if roster is not None:
        week = roster.week
        if week is not None and week != current_week:
            messages.error(request, "Cette semaine est terminée : son équipe ne se modifie plus.")
            return redirect("cards:weekly")
    else:
        week = current_week if request.GET.get("semaine") or request.POST.get("semaine") else None
        if week is not None:
            roster = Roster.objects.filter(user=request.user, week=week).first()
    back = "cards:weekly" if week else "cards:rosters"
    if request.method == "POST":
        try:
            picks = json.loads(request.POST.get("picks") or "{}")
        except ValueError:
            picks = {}
        try:
            roster = save_roster(request.user, request.POST.get("name", ""), picks, roster, week=week)
        except (GameError, IntegrityError) as exc:
            messages.error(request, str(exc) if isinstance(exc, GameError) else "Tu as déjà une équipe cette semaine.")
            data = _builder_data(request.user, roster, week)
            data["name"] = request.POST.get("name", "")
            data["picks"] = picks
            return render(request, "cards/roster_edit.html", {"roster": roster, "data": data, "week": week})
        if roster.score is not None:
            messages.success(request, f"« {roster.name} » enregistrée : {roster.score:.1f} points.")
        else:
            messages.success(request, f"« {roster.name} » enregistrée. Complète les 5 compositions pour être classé.")
        return redirect(back)
    return render(request, "cards/roster_edit.html", {
        "roster": roster, "data": _builder_data(request.user, roster, week), "week": week,
        "rules": _rule_list(weekly.meta_for(week).rules) if week else [],
    })


@login_required
@require_POST
def roster_preview(request):
    """Le score d'une équipe en cours de composition, sans l'enregistrer."""
    try:
        body = json.loads(request.body or b"{}")
        picks = body.get("picks", {})
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Requête invalide."}, status=400)
    week = weekly.week_start() if body.get("week") else None
    return JsonResponse(evaluate_roster(request.user, picks, week=week))


@login_required
@require_POST
def roster_delete(request, pk):
    roster = get_object_or_404(Roster, pk=pk, user=request.user, week__isnull=True)
    roster.delete()
    messages.success(request, f"« {roster.name} » supprimée.")
    return redirect("cards:rosters")


def _lineup_cards(roster, best: dict) -> list[dict]:
    """Les 5 cartes d'une équipe dans l'ordre des postes, avec la rareté
    retenue pour son score et son premier champion."""
    by_role = {slot.role: slot for slot in roster.slots.all()}
    lineup = []
    for role in ROLES:
        slot = by_role.get(role)
        if slot is None:
            continue
        rarity = best.get((roster.user_id, slot.card_id))
        champions = slot.champion_list()
        lineup.append({"card": slot.card, "role": role, "rarity": rarity,
                       "champion": champions[0] if champions else None})
    return lineup


def ranking(request):
    """Le classement général (meilleure équipe permanente complète de
    chaque joueur) ou celui de la semaine (?semaine=1)."""
    week = None
    if request.GET.get("semaine"):
        week = weekly.week_start()
        weekly.ensure_weekly_rosters(week)
    rows = leaderboard(week=week)
    # La meilleure rareté de chaque carte alignée, en une requête.
    best = {}
    owned = UserCard.objects.filter(
        user__in=[r.user_id for _, r in rows],
        card__in=[slot.card_id for _, r in rows for slot in r.slots.all()],
    ).select_related("rarity")
    for copy in owned:
        key = (copy.user_id, copy.card_id)
        if key not in best or copy.rarity.order > best[key].order:
            best[key] = copy.rarity
    for _, roster in rows:
        roster.lineup = _lineup_cards(roster, best)
    mine = None
    if request.user.is_authenticated:
        mine = next(((rank, r) for rank, r in rows if r.user_id == request.user.pk), None)
    return render(request, "cards/ranking.html", {"rows": rows, "mine": mine, "week": week,
                                                  "ends_at": weekly.week_end(week) if week else None,
                                                  "podium": rows[:3], "rest": rows[3:]})
