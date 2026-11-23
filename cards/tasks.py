"""Les tâches planifiées du jeu (planning : esport/schedules.py)."""
from typing import Any

from django.utils.translation import gettext_lazy as _

from generic.tasks import managed_task

from . import fantasy


@managed_task(
    name="cards.fantasy_tick",
    label=_("Fantasy : semaines et points"),
    description=_(
        "Compétitions, verrouillage du mardi soir, matchs simulés de l'international fictif, points, "
        "comptage définitif et boosters."
    ),
    icon="sports_score",
    announce=("page",),
    report=("page",),
)
def fantasy_tick_task(run: Any) -> str:
    stats = fantasy.tick(log=run.note)
    return ", ".join(f"{v} {k}" for k, v in stats.items() if v) or "Rien de neuf"


try:
    from celery import shared_task
except ImportError:
    shared_task = None

if shared_task is not None:
    @shared_task(name="cards.fantasy_hourly", ignore_result=True)
    def fantasy_hourly() -> dict:
        """Toutes les heures, sans laisser de trace dans la gestion : la
        Fantasy avance (verrouillage, simulation, points, récompenses)."""
        return fantasy.tick()
