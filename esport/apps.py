from django.apps import AppConfig


class EsportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "esport"
    verbose_name = "Données esport (Leaguepedia)"

    def ready(self):
        from . import tasks  # noqa: F401 - les opérations et tâches planifiées, déclarées au démarrage
