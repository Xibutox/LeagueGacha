"""Le planning des mises à jour automatiques (django-celery-beat).

| Tâche                         | Quand                          | Coût                                  |
|-------------------------------|--------------------------------|---------------------------------------|
| esport.watch_results          | toutes les 10 min              | 1 requête en base, rien sinon          |
| esport.import_results         | lancée par la précédente       | parties récentes d'un tournoi          |
| esport.sync_schedules         | 06:20 et 13:20                 | 1 à 3 requêtes Leaguepedia             |
| esport.discover_tournaments   | lundi et jeudi 05:10           | 1 à 2 requêtes Leaguepedia             |
| esport.consolidate            | 04:30                          | stats et cartes des splits du jour     |
| cards.fantasy_hourly          | toutes les heures à :05        | calcul en base                         |

Les heures sont celles de Paris. ensure_schedules() crée ce qui manque et
ne touche pas à un planning modifié dans la gestion (sauf force=True).
"""
from __future__ import annotations

from django.apps import apps

SCHEDULES = [
    # nom, tâche, intervalle (minutes) ou crontab (minute, heure, jours de la semaine)
    ("Résultats : surveillance", "esport.watch_results", 10, None),
    ("Fantasy : semaines et points", "cards.fantasy_hourly", None, ("5", "*", "*")),
    ("Calendrier des matchs", "esport.sync_schedules", None, ("20", "6,13", "*")),
    ("Tournois à venir", "esport.discover_tournaments", None, ("10", "5", "1,4")),
    ("Mise à jour de nuit", "esport.consolidate", None, ("30", "4", "*")),
]


def available() -> bool:
    return apps.is_installed("django_celery_beat")


def ensure_schedules(force: bool = False, timezone_name: str = "Europe/Paris") -> list[str]:
    """Crée les plannings manquants. Renvoie les noms créés (ou mis à jour)."""
    if not available():
        return []
    from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

    done = []
    for name, task, minutes, cron in SCHEDULES:
        existing = PeriodicTask.objects.filter(name=name).first()
        if existing is not None and not force:
            continue
        values = {"task": task, "enabled": True, "interval": None, "crontab": None}
        if minutes:
            values["interval"], _ = IntervalSchedule.objects.get_or_create(every=minutes,
                                                                           period=IntervalSchedule.MINUTES)
        else:
            minute, hour, dow = cron
            values["crontab"], _ = CrontabSchedule.objects.get_or_create(
                minute=minute, hour=hour, day_of_week=dow, day_of_month="*", month_of_year="*",
                timezone=timezone_name)
        PeriodicTask.objects.update_or_create(name=name, defaults=values)
        done.append(name)
    return done
