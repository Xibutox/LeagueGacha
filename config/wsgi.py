import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()


def _warm_up() -> None:
    """Charge d'avance les adresses et les gabarits du jeu : sinon chaque
    processus Gunicorn paie ce chargement sur la première page qu'il sert
    (plus d'une seconde sur un Raspberry Pi). Avec --preload, c'est fait une
    fois, avant que les processus ne se partagent le travail."""
    from django.template.loader import get_template
    from django.urls import get_resolver

    get_resolver().url_patterns  # noqa: B018 - importe toutes les vues
    for name in (
        "base.html", "cards/home.html", "cards/_card.html", "cards/_pack_art.html", "cards/opening.html",
        "cards/collection.html", "cards/card_detail.html", "cards/rosters.html", "cards/roster_edit.html",
        "cards/ranking.html", "cards/tournament.html", "cards/tournament_series.html",
    ):
        try:
            get_template(name)
        except Exception:  # noqa: BLE001 - un gabarit manquant ne doit pas empêcher le démarrage
            pass


if os.environ.get("WARM_UP", "1") == "1":
    _warm_up()
