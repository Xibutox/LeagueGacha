"""Les pages et l'API JSON de la Faille (cards/faille.py)."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import faille
from .faille import FailleError


@login_required
def faille_page(request):
    if not faille.is_ready():
        return render(request, "cards/faille.html", {"ready": False})
    run = faille.get_run(request.user)
    offline = faille.credit_offline(run)
    data = faille.page_data(run)
    data["offlineGold"] = round(offline)
    refund = run.state.pop("refund", None)
    if refund:
        data["refund"] = refund
        run.save(update_fields=["state"])
    data["urls"] = {key: reverse(f"cards:faille_{name}") for key, name in (
        ("sync", "sync"), ("buy", "buy"), ("sell", "sell"), ("ornn", "ornn"), ("elixir", "elixir"),
        ("augment", "augment"), ("enemies", "enemies"))}
    data["urls"]["ranking"] = reverse("cards:ranking") + "?faille=1"
    return render(request, "cards/faille.html", {"ready": True, "data": data})


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def _answer(run, extra=None, status=200):
    payload = {"state": faille.public_state(run)}
    payload.update(extra or {})
    return JsonResponse(payload, status=status)


@login_required
@require_POST
def faille_sync(request):
    run = faille.get_run(request.user)
    result = faille.apply_sync(run, _body(request))
    if not result["ok"]:
        return _answer(run, {"error": result["reason"]}, status=409)
    return _answer(run, {"events": result["new"], "gold": result.get("gold", 0)})


def _action(request, func, *args):
    run = faille.get_run(request.user)
    try:
        extra = func(run, *args)
    except FailleError as exc:
        return _answer(run, {"error": str(exc)}, status=400)
    return _answer(run, extra)


@login_required
@require_POST
def faille_buy(request):
    return _action(request, faille.buy, str(_body(request).get("item", "")))


@login_required
@require_POST
def faille_sell(request):
    return _action(request, faille.sell, _body(request).get("slot"))


@login_required
@require_POST
def faille_ornn(request):
    return _action(request, faille.ornn, _body(request).get("slot"))


@login_required
@require_POST
def faille_elixir(request):
    return _action(request, faille.buy_elixir, str(_body(request).get("item", "")))


@login_required
@require_POST
def faille_augment(request):
    return _action(request, faille.choose_augment, str(_body(request).get("code", "")))


@login_required
def faille_enemies(request):
    run = faille.get_run(request.user)
    try:
        start = max(0, int(request.GET.get("from", 0)))
    except ValueError:
        start = 0
    start = min(start, int(run.state.get("best", 0)) + faille.ENEMY_WINDOW)
    return JsonResponse({"enemies": faille.enemies(run.state, run.week, start)})
