import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from esport.models import Champion, Split
from esport.scoring import COHERENCE_WEIGHT, DIMENSIONS, SLOT_PLAYER_WEIGHT, UNPLAYED_PENALTY

from .models import COMPOSITIONS, ROLES, Booster, Card, CardChampion, PackOpening, Rarity, Roster, UserCard
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
        if b.card_pool().exists()
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
    context = {"boosters": boosters, "portals": portals, "rarities": Rarity.objects.all()}
    if request.user.is_authenticated:
        context["exchanges"] = list(pending_exchanges(request.user))
    if request.user.is_authenticated:
        context["unlimited"] = any(b.status["unlimited"] for b in boosters)
        context["ready"] = sum(b.status["available"] for b in boosters)
        context["owned_count"] = UserCard.objects.filter(user=request.user).values("card").distinct().count()
        context["total_count"] = _playable_cards().count()
        context["recent_openings"] = PackOpening.objects.filter(user=request.user).select_related("booster")[:5]
        context["gifts"] = unseen_gifts(request.user)
        best = Roster.objects.filter(user=request.user, score__isnull=False).order_by("-score").first()
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
    opening = get_object_or_404(PackOpening.objects.select_related("booster"), pk=pk, user=request.user)
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
    status = None
    if opening.booster:
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
        },
    )


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

    cards = cards.order_by("split__start_date", "-overall", "player__ign")
    rows = []
    for card in cards:
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
    )
    return render(
        request,
        "cards/collection.html",
        {
            "rows": rows,
            "splits": Split.objects.filter(is_playable=True, cards__isnull=False).distinct(),
            "rarities": Rarity.objects.all(),
            "roles": ROLES,
            "progress": progress,
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
@login_required
def roster_list(request):
    rosters = Roster.objects.filter(user=request.user).prefetch_related(
        "slots__card__player", "slots__card__split", "slots__card__team", "slots__card__image",
        "slots__card__stats", "slots__picks__champion",
    ).order_by("-score", "-updated_at")
    owned = list(Card.objects.filter(owners__user=request.user).values_list("pk", flat=True).distinct())
    rarities = best_rarities(request.user, owned)
    items = []
    for roster in rosters:
        slots = roster.slot_map()
        items.append({
            "roster": roster,
            "slots": [(role, slots.get(role), rarities.get(slots[role].card_id) if role in slots else None)
                      for role in ROLES],
        })
    return render(request, "cards/rosters.html", {"items": items})


def _builder_data(user, roster):
    owned_ids = list(Card.objects.filter(owners__user=user, owners__quantity__gt=0).values_list("pk", flat=True).distinct())
    rarities = best_rarities(user, owned_ids)
    cards = (
        Card.objects.filter(pk__in=owned_ids)
        .select_related("player", "split", "team", "image", "stats")
        .prefetch_related(Prefetch("champions", queryset=CardChampion.objects.only(
            "card_id", "champion_id", "rating", "games", "wins", "history_games")))
        .order_by("-overall", "player__ign")
    )
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
        "name": roster.name if roster else "",
        "picks": roster_picks(roster) if roster else {},
        "previewUrl": reverse("cards:roster_preview"),
    }


@login_required
def roster_edit(request, pk=None):
    roster = get_object_or_404(Roster, pk=pk, user=request.user) if pk else None
    if request.method == "POST":
        try:
            picks = json.loads(request.POST.get("picks") or "{}")
        except ValueError:
            picks = {}
        try:
            roster = save_roster(request.user, request.POST.get("name", ""), picks, roster)
        except GameError as exc:
            messages.error(request, str(exc))
            data = _builder_data(request.user, roster)
            data["name"] = request.POST.get("name", "")
            data["picks"] = picks
            return render(request, "cards/roster_edit.html", {"roster": roster, "data": data})
        if roster.score is not None:
            messages.success(request, f"« {roster.name} » enregistrée : {roster.score:.1f} points.")
        else:
            messages.success(request, f"« {roster.name} » enregistrée. Complète les 5 compositions pour être classé.")
        return redirect("cards:rosters")
    return render(request, "cards/roster_edit.html", {"roster": roster, "data": _builder_data(request.user, roster)})


@login_required
@require_POST
def roster_preview(request):
    """Le score d'une équipe en cours de composition, sans l'enregistrer."""
    try:
        picks = json.loads(request.body or b"{}").get("picks", {})
    except ValueError:
        return JsonResponse({"error": "Requête invalide."}, status=400)
    return JsonResponse(evaluate_roster(request.user, picks))


@login_required
@require_POST
def roster_delete(request, pk):
    roster = get_object_or_404(Roster, pk=pk, user=request.user)
    roster.delete()
    messages.success(request, f"« {roster.name} » supprimée.")
    return redirect("cards:rosters")


def ranking(request):
    """Le classement général : la meilleure équipe complète de chaque joueur."""
    rows = leaderboard()
    mine = None
    if request.user.is_authenticated:
        mine = next(((rank, r) for rank, r in rows if r.user_id == request.user.pk), None)
    return render(request, "cards/ranking.html", {"rows": rows, "mine": mine,
                                                  "podium": rows[:3], "rest": rows[3:]})
