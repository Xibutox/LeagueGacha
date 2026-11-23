"""Le planning des mises à jour automatiques, lu par `celery beat`
(CELERY_BEAT_SCHEDULE dans config/settings/base.py).

| Tâche                         | Quand                          | Coût                                  |
|-------------------------------|--------------------------------|---------------------------------------|
| esport.watch_results          | toutes les 10 min              | 1 requête en base, rien sinon          |
| esport.import_results         | lancée par la précédente       | parties récentes d'un tournoi          |
| esport.sync_schedules         | 06:20 et 13:20                 | 1 à 3 requêtes Leaguepedia             |
| esport.discover_tournaments   | lundi et jeudi 05:10           | 1 à 2 requêtes Leaguepedia             |
| esport.consolidate            | 04:30                          | stats et cartes des splits du jour     |
| cards.fantasy_hourly          | toutes les heures à :05        | calcul en base                         |

Heures de Paris (CELERY_TIMEZONE). Pour changer un horaire : ici, puis
redéployer. (django-celery-beat, qui permettrait de le faire depuis la
gestion, ne gère pas encore Django 6.1.)
"""
from __future__ import annotations

SCHEDULES = [
    # nom, tâche, intervalle (minutes) ou crontab (minute, heure, jours de la semaine)
    ("Résultats : surveillance", "esport.watch_results", 10, None),
    ("Fantasy : semaines et points", "cards.fantasy_hourly", None, ("5", "*", "*")),
    ("Calendrier des matchs", "esport.sync_schedules", None, ("20", "6,13", "*")),
    ("Tournois à venir", "esport.discover_tournaments", None, ("10", "5", "1,4")),
    ("Mise à jour de nuit", "esport.consolidate", None, ("30", "4", "*")),
]


def beat_schedule() -> dict:
    """Le planning au format de Celery (vide sans Celery)."""
    try:
        from celery.schedules import crontab
    except ImportError:
        return {}
    out = {}
    for name, task, minutes, cron in SCHEDULES:
        if minutes:
            schedule = minutes * 60.0
        else:
            minute, hour, dow = cron
            schedule = crontab(minute=minute, hour=hour, day_of_week=dow)
        out[name] = {"task": task, "schedule": schedule}
    return out
