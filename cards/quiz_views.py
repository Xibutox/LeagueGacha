"""Les pages du quiz (voir cards/quiz.py)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import quiz
from .models import QuizAttempt
from .services import GameError, copies_by_card


@login_required
def quiz_page(request):
    attempt = quiz.current_attempt(request.user)
    context = {"attempt": attempt, "next_at": quiz.next_period(), "period_hours": quiz.PERIOD_HOURS}
    if attempt:
        index = attempt.current
        context["question"] = attempt.questions[index] if index is not None else None
        context["number"] = (index or 0) + 1
        answered = [q for q in attempt.questions if q.get("result") is not None]
        context["answered"] = answered
        # La dernière réponse donnée (bonne, mauvaise, ou essai raté).
        last = None
        for q in attempt.questions:
            if q.get("picked"):
                last = q
        context["last"] = last
        if attempt.finished_at:
            choices = list(attempt.reward_choices.select_related("player", "split", "team", "image", "stats"))
            owned = copies_by_card(request.user, [c.pk for c in choices])
            for c in choices:
                c.owned = sum(q for _, q in owned.get(c.pk, []))
            context["choices"] = choices
    return render(request, "cards/quiz.html", context)


@login_required
@require_POST
def quiz_start(request):
    try:
        quiz.start(request.user)
    except GameError as exc:
        messages.error(request, str(exc))
    return redirect("cards:quiz")


@login_required
@require_POST
def quiz_answer(request, pk):
    attempt = get_object_or_404(QuizAttempt, pk=pk, user=request.user)
    try:
        quiz.answer(request.user, attempt, request.POST.getlist("choix"))
    except GameError as exc:
        messages.error(request, str(exc))
    return redirect("cards:quiz")


@login_required
@require_POST
def quiz_choose(request, pk):
    attempt = get_object_or_404(QuizAttempt, pk=pk, user=request.user)
    try:
        card = quiz.choose(request.user, attempt, int(request.POST.get("card", 0)))
    except (GameError, ValueError) as exc:
        messages.error(request, str(exc) or "Choix impossible.")
        return redirect("cards:quiz")
    messages.success(request, f"{card.player.ign} rejoint ta collection en {attempt.reward_rarity.name.lower()}.")
    return redirect("cards:card_detail", pk=card.pk)
