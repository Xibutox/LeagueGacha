"""Ce que la barre du haut affiche sur chaque page du jeu."""


def game(request):
    from django.conf import settings

    context = {"app_version": settings.APP_VERSION}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or request.path.startswith("/gestion/"):
        return context

    def ready():
        # Calculé seulement si le gabarit l'affiche.
        from .models import Booster
        from .services import booster_status

        boosters = [b for b in Booster.objects.filter(is_active=True) if b.card_pool().exists()]
        status = booster_status(user, boosters)
        if any(s["unlimited"] for s in status.values()):
            return "∞"
        return sum(s["available"] for s in status.values())

    context["packs_ready"] = ready
    return context
